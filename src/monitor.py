"""
工作流监控器模块

负责持续监控工作流状态，并触发恢复流程
"""

import time
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Callable
from threading import Event

from .api_client import DolphinSchedulerClient, WorkflowInstance, ProcessDefinition
from .task_validator import TaskValidator
from .recovery_handler import RecoveryHandler, RecoveryResult
from .config import Config, ProjectConfig
from .logger import get_logger
from .notifiers import create_notification_manager, NotificationManager
from .notifiers.message_builder import (
    build_failure_detected_message,
    build_recovery_success_message,
    build_recovery_failed_message,
    build_threshold_exceeded_message
)
from .notifiers.rate_limiter import NotificationRateLimiter
from .schedule_tracker import ScheduleTracker, WorkflowPeriodStatus


@dataclass
class MonitorStats:
    """监控统计信息"""
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    check_count: int = 0
    failed_workflows_found: int = 0
    recovery_attempts: int = 0
    successful_recoveries: int = 0
    skipped_due_to_threshold: int = 0  # 因超过阈值而跳过恢复的数量
    skipped_due_to_schedule: int = 0   # 因调度状态跳过的 API 调用数量
    api_calls_saved: int = 0           # 节省的 API 调用次数
    last_check_time: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    # 24小时内失败工作流统计（按项目和工作流分组）
    workflow_failure_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)  # {project_name: {workflow_name: count}}


@dataclass
class MonitoredProject:
    """被监控的项目"""
    config: ProjectConfig
    project_code: Optional[int] = None
    workflow_codes: Dict[str, int] = field(default_factory=dict)
    last_check_time: Optional[str] = None
    status: str = "pending"


class WorkflowMonitor:
    """工作流监控器"""

    def __init__(
        self,
        client: DolphinSchedulerClient,
        validator: TaskValidator,
        recovery_handler: RecoveryHandler,
        config: Config
    ):
        """
        初始化监控器

        Args:
            client: DolphinScheduler API 客户端
            validator: 任务验证器
            recovery_handler: 恢复处理器
            config: 配置
        """
        self.client = client
        self.validator = validator
        self.recovery_handler = recovery_handler
        self.config = config
        self.logger = get_logger()

        # 初始化通知管理器
        self.notification_manager = create_notification_manager(config.notification)

        # 初始化通知限流器（24小时内最多6次）
        self.notification_rate_limiter = NotificationRateLimiter(
            time_window_hours=24,
            max_notifications=6
        )

        # 初始化调度状态追踪器
        self.schedule_tracker = ScheduleTracker(
            state_file="data/schedule_state.json",
            execution_window_hours=getattr(config.monitor, 'execution_window_hours', 4),
            success_cooldown_minutes=getattr(config.monitor, 'success_cooldown_minutes', 30)
        )
        self.enable_schedule_optimization = getattr(
            config.monitor, 'enable_schedule_optimization', True
        )

        # 监控状态
        self.stats = MonitorStats()
        self.monitored_projects: List[MonitoredProject] = []
        self._stop_event = Event()
        self._running = False

        # 回调函数
        self._on_failure_detected: Optional[Callable] = None
        self._on_recovery_executed: Optional[Callable] = None

        # 初始化被监控的项目
        self._init_monitored_projects()

    def _init_monitored_projects(self) -> None:
        """初始化被监控的项目列表"""
        for project_config in self.config.projects:
            self.monitored_projects.append(MonitoredProject(config=project_config))

    def _resolve_project_codes(self) -> bool:
        """解析项目编码"""
        all_projects = self.client.get_projects()
        project_map = {p.name: p for p in all_projects}

        success = True
        for monitored in self.monitored_projects:
            project = project_map.get(monitored.config.name)
            if project:
                monitored.project_code = project.code
                monitored.status = "active"
                self.logger.info(f"找到项目: {monitored.config.name} (code: {project.code})")

                # 获取项目下的工作流定义
                workflows = self.client.get_process_definitions(project.code)
                workflow_map = {w.name: w.code for w in workflows}

                # 根据配置确定需要查询调度的工作流
                if not monitored.config.monitor_all and monitored.config.workflows:
                    # 只查询配置的工作流的调度信息
                    target_workflow_codes = [
                        workflow_map[wf_name]
                        for wf_name in monitored.config.workflows
                        if wf_name in workflow_map
                    ]
                else:
                    # monitor_all=true，查询所有工作流
                    target_workflow_codes = None

                # 获取工作流调度信息（用于智能调度监控）
                schedule_map = {}
                if self.enable_schedule_optimization:
                    schedule_map = self.client.get_workflow_schedule_map(
                        project.code, target_workflow_codes
                    )
                    self.logger.info(f"  获取到 {len(schedule_map)} 个工作流调度信息")

                # 如果不是监控所有工作流，需要解析工作流编码
                if not monitored.config.monitor_all and monitored.config.workflows:
                    for wf_name in monitored.config.workflows:
                        if wf_name in workflow_map:
                            wf_code = workflow_map[wf_name]
                            monitored.workflow_codes[wf_name] = wf_code
                            self.logger.debug(f"  - 工作流: {wf_name} (code: {wf_code})")

                            # 注册工作流调度信息
                            if wf_code in schedule_map:
                                schedule = schedule_map[wf_code]
                                self.schedule_tracker.register_workflow(
                                    project_code=project.code,
                                    project_name=monitored.config.name,
                                    workflow_code=wf_code,
                                    workflow_name=wf_name,
                                    cron_expression=schedule.crontab
                                )
                                self.logger.debug(f"    调度: {schedule.crontab}")
                        else:
                            self.logger.warning(f"  - 未找到工作流: {wf_name}")
                else:
                    # monitor_all=true 时，注册所有有调度的工作流
                    for wf in workflows:
                        if wf.code in schedule_map:
                            schedule = schedule_map[wf.code]
                            self.schedule_tracker.register_workflow(
                                project_code=project.code,
                                project_name=monitored.config.name,
                                workflow_code=wf.code,
                                workflow_name=wf.name,
                                cron_expression=schedule.crontab
                            )
            else:
                monitored.status = "not_found"
                self.logger.warning(f"未找到项目: {monitored.config.name}")
                success = False

        return success

    def set_callbacks(
        self,
        on_failure_detected: Optional[Callable] = None,
        on_recovery_executed: Optional[Callable] = None
    ) -> None:
        """
        设置回调函数

        Args:
            on_failure_detected: 检测到失败时的回调
            on_recovery_executed: 执行恢复后的回调
        """
        self._on_failure_detected = on_failure_detected
        self._on_recovery_executed = on_recovery_executed

    def check_once(self) -> List[RecoveryResult]:
        """
        执行一次检查

        Returns:
            恢复结果列表
        """
        results: List[RecoveryResult] = []
        self.stats.check_count += 1
        self.stats.last_check_time = datetime.now().isoformat()

        self.logger.info(f"开始第 {self.stats.check_count} 次检查...")

        for monitored in self.monitored_projects:
            if monitored.status != "active" or monitored.project_code is None:
                continue

            monitored.last_check_time = datetime.now().isoformat()

            try:
                project_results = self._check_project(monitored)
                results.extend(project_results)
            except Exception as e:
                error_msg = f"检查项目 {monitored.config.name} 时出错: {str(e)}"
                self.logger.error(error_msg)
                self.stats.errors.append(error_msg)

        self.logger.info(
            f"检查完成 | 发现失败工作流: {len(results)} | "
            f"尝试恢复: {sum(1 for r in results if r.recovery_executed)} | "
            f"恢复成功: {sum(1 for r in results if r.recovery_success)}"
        )

        return results

    def _extract_workflow_name(self, workflow_instance_name: str) -> str:
        """
        从工作流实例名称中提取工作流名称

        工作流实例名称格式示例：
        - 新加坡日任务调度-8-20251230023001444
        - 数据ETL-20251230120000

        Args:
            workflow_instance_name: 工作流实例名称

        Returns:
            工作流名称（移除时间戳和运行次数）
        """
        import re
        # 移除末尾的-加时间戳（例如：-20251230023001444）
        wf_name = re.sub(r'-\d{14,20}$', '', workflow_instance_name)
        # 如果还有-加数字-时间戳，可能是运行次数（例如：-8-时间戳），也移除
        wf_name = re.sub(r'-\d+-\d{14,20}$', '', workflow_instance_name)
        # 如果全部被移除，使用原名称
        return wf_name if wf_name else workflow_instance_name

    def _is_within_time_window(self, workflow: WorkflowInstance, hours: int = 24) -> bool:
        """
        检查工作流是否在指定时间窗口内

        Args:
            workflow: 工作流实例
            hours: 时间窗口（小时）

        Returns:
            是否在时间窗口内
        """
        if not workflow.start_time:
            return False

        try:
            # 解析启动时间（DolphinScheduler 格式: "2025-12-30 07:09:24"）
            start_time = datetime.strptime(workflow.start_time, "%Y-%m-%d %H:%M:%S")
            time_threshold = datetime.now() - timedelta(hours=hours)

            return start_time >= time_threshold
        except (ValueError, TypeError) as e:
            self.logger.warning(f"无法解析工作流 {workflow.name} 的启动时间: {workflow.start_time}")
            return False

    def _check_project(self, monitored: MonitoredProject) -> List[RecoveryResult]:
        """
        检查单个项目

        Args:
            monitored: 被监控的项目

        Returns:
            恢复结果列表
        """
        results: List[RecoveryResult] = []
        project_code = monitored.project_code

        self.logger.debug(f"检查项目: {monitored.config.name}")

        # 获取失败的工作流实例
        # 优化：使用调度状态追踪器减少不必要的 API 调用
        if monitored.config.monitor_all:
            # 监控所有工作流（获取项目下所有失败工作流）
            self.logger.debug(f"项目 {monitored.config.name} 配置为监控所有工作流")
            failed_instances = self.client.get_failed_workflow_instances(project_code)
        else:
            # 只监控指定的工作流
            if not monitored.workflow_codes:
                self.logger.warning(
                    f"项目 {monitored.config.name} 未配置具体工作流且 monitor_all=false，"
                    f"将不会监控任何工作流"
                )
                return results

            # ============================================================
            # 优化：智能调度监控
            # 1. 使用调度状态追踪器过滤需要监控的工作流
            # 2. 批量查询项目所有失败实例，本地过滤
            # ============================================================
            workflow_codes_list = list(monitored.workflow_codes.values())
            workflows_to_check = workflow_codes_list
            skipped_count = 0

            if self.enable_schedule_optimization:
                # 获取监控决策
                to_monitor, decisions = self.schedule_tracker.get_workflows_to_monitor(
                    project_code, workflow_codes_list
                )

                # 记录跳过的工作流
                for decision in decisions:
                    if not decision.should_query_api:
                        skipped_count += 1
                        self.logger.debug(
                            f"  跳过工作流 [{decision.workflow_name}]: {decision.reason}"
                        )

                workflows_to_check = to_monitor
                self.stats.skipped_due_to_schedule += skipped_count
                self.stats.api_calls_saved += skipped_count

            self.logger.debug(
                f"项目 {monitored.config.name}: "
                f"配置 {len(workflow_codes_list)} 个工作流，"
                f"需检查 {len(workflows_to_check)} 个，跳过 {skipped_count} 个"
            )

            # 如果没有需要检查的工作流，直接返回
            if not workflows_to_check:
                self.logger.debug(f"项目 {monitored.config.name} 所有工作流都跳过检查")
                return results

            # 优化：批量查询 + 本地过滤（替代逐个查询）
            # 只调用一次 API 获取项目所有失败实例
            all_failed_instances = self.client.get_failed_workflow_instances(project_code)

            # 本地过滤：只保留需要检查的工作流
            target_codes = set(workflows_to_check)
            failed_instances = [
                wf for wf in all_failed_instances
                if wf.process_definition_code in target_codes
            ]

            # 记录优化效果
            original_api_calls = len(workflow_codes_list)
            actual_api_calls = 1  # 批量查询只需 1 次
            saved_calls = original_api_calls - actual_api_calls
            self.stats.api_calls_saved += saved_calls

            if failed_instances:
                self.logger.debug(
                    f"项目 {monitored.config.name} 过滤后: {len(failed_instances)} 个失败实例"
                )

        if not failed_instances:
            self.logger.debug(f"项目 {monitored.config.name} 中没有失败的工作流")
            return results

        # 过滤：只保留配置时间窗口内启动的工作流
        time_window_hours = self.config.monitor.time_window_hours
        recent_failed = [
            wf for wf in failed_instances
            if self._is_within_time_window(wf, time_window_hours)
        ]

        # 统计被过滤的数量（启动时间超过24小时的不记录日志）
        filtered_count = len(failed_instances) - len(recent_failed)

        if not recent_failed:
            if filtered_count > 0:
                self.logger.debug(
                    f"项目 {monitored.config.name} 中有 {filtered_count} 个失败工作流，"
                    f"但启动时间都超过 {time_window_hours} 小时，已忽略"
                )
            return results

        self.logger.info(
            f"项目 {monitored.config.name} 中发现 {len(recent_failed)} 个失败的工作流实例"
            f"（{time_window_hours}小时内启动）"
        )
        self.stats.failed_workflows_found += len(recent_failed)

        # ============================================================
        # 失败阈值判断逻辑
        # ============================================================
        # 1. 统计的是：工作流实例（WorkflowInstance）的失败次数
        # 2. 不统计：工作流内部的子工作流和任务失败数
        # 3. 分组依据：process_definition_code（工作流定义编码）
        #    - 同一个工作流定义的所有失败实例归为一组
        #    - 例如："数据ETL" 工作流在24小时内失败了3次，就是3个实例
        # 4. 时间范围：只统计24小时内启动的工作流实例
        # ============================================================
        from collections import defaultdict
        workflow_groups = defaultdict(list)
        for wf in recent_failed:
            # wf 是 WorkflowInstance 对象（工作流实例）
            # 按工作流定义编码分组
            workflow_groups[wf.process_definition_code].append(wf)

        # 输出统计信息并记录到全局统计
        project_name = monitored.config.name
        if project_name not in self.stats.workflow_failure_stats:
            self.stats.workflow_failure_stats[project_name] = {}

        self.logger.info(f"失败工作流统计（按工作流定义分组）:")
        for def_code, instances in workflow_groups.items():
            # 提取工作流名称
            wf_name = self._extract_workflow_name(instances[0].name) if instances else "未知"

            self.logger.info(
                f"  - [{wf_name}] (定义码:{def_code}): {len(instances)} 个失败实例"
            )
            # 记录到统计
            self.stats.workflow_failure_stats[project_name][wf_name] = len(instances)

        # ============================================================
        # 阈值判断：针对每个工作流定义，独立检查失败实例数量
        # ============================================================
        # 判断逻辑：
        # - 统计同一个工作流定义在24小时内的失败实例数量
        # - 如果失败实例数量 > 阈值 → 只通知不恢复
        # - 如果失败实例数量 <= 阈值 → 可以尝试恢复
        #
        # 示例：
        #   工作流A: 3个失败实例 > 阈值(1) → 只通知
        #   工作流B: 1个失败实例 <= 阈值(1) → 尝试恢复
        #   工作流C: 1个失败实例 <= 阈值(1) → 尝试恢复
        # ============================================================
        max_failures_threshold = self.config.monitor.max_failures_for_recovery
        workflows_to_recover = []
        workflows_to_notify_only = []

        for def_code, instances in workflow_groups.items():
            # 提取工作流名称
            wf_name = self._extract_workflow_name(instances[0].name) if instances else "未知"

            # 判断该工作流定义的失败实例数量是否超过阈值
            failure_count = len(instances)  # 该工作流定义的失败实例数量

            if failure_count > max_failures_threshold:
                # 超过阈值：该工作流短时间内多次失败，只通知不恢复
                workflows_to_notify_only.extend(instances)
                self.stats.skipped_due_to_threshold += len(instances)
                self.logger.warning(
                    f"⚠️  工作流 [{wf_name}] 在 {time_window_hours} 小时内有 {failure_count} 个实例失败，"
                    f"超过阈值({max_failures_threshold}个)，只通知不恢复"
                )

                # 发送超过阈值通知（带限流控制）
                if instances and self.notification_manager.has_notifiers():
                    # 检查是否可以发送通知（24小时内最多6次）
                    if self.notification_rate_limiter.can_notify(
                        project_name=monitored.config.name,
                        workflow_definition_code=def_code,
                        workflow_name=wf_name
                    ):
                        message = build_threshold_exceeded_message(
                            workflow=instances[0],
                            project_name=monitored.config.name,
                            failure_count=failure_count,
                            threshold=max_failures_threshold,
                            time_window=time_window_hours
                        )
                        self.notification_manager.send(message)

                        # 记录本次通知
                        self.notification_rate_limiter.record_notification(
                            project_name=monitored.config.name,
                            workflow_definition_code=def_code,
                            workflow_name=wf_name
                        )

                        remaining = self.notification_rate_limiter.get_remaining_notifications(
                            project_name=monitored.config.name,
                            workflow_definition_code=def_code
                        )
                        self.logger.info(
                            f"已发送超过阈值通知，24小时内还可发送 {remaining} 次通知"
                        )
                    else:
                        # 超过通知限制
                        count = self.notification_rate_limiter.get_notification_count(
                            project_name=monitored.config.name,
                            workflow_definition_code=def_code
                        )
                        self.logger.warning(
                            f"工作流 [{wf_name}] 在24小时内已发送 {count} 次通知，"
                            f"达到上限(6次)，已跳过本次通知"
                        )
            else:
                # 未超过阈值：可以尝试自动恢复
                workflows_to_recover.extend(instances)
                self.logger.debug(
                    f"工作流 [{wf_name}] 失败 {failure_count} 个实例，"
                    f"未超过阈值({max_failures_threshold})，将尝试恢复"
                )

        # 输出超过阈值的工作流详情
        if workflows_to_notify_only:
            self.logger.warning(f"超过阈值的失败工作流实例列表（只通知不恢复）:")
            for i, wf in enumerate(workflows_to_notify_only, 1):
                self.logger.warning(
                    f"  {i}. {wf.name} (ID:{wf.id}, 启动时间:{wf.start_time})"
                )

            # 触发失败检测回调（用于发送通知）
            if self._on_failure_detected:
                for instance in workflows_to_notify_only:
                    self._on_failure_detected(instance)

        # 处理未超过阈值的失败工作流
        if workflows_to_recover:
            self.logger.info(
                f"将尝试恢复 {len(workflows_to_recover)} 个工作流实例"
            )

        for instance in workflows_to_recover:
            # 触发失败检测回调（保留旧的回调接口）
            if self._on_failure_detected:
                self._on_failure_detected(instance)

            # 处理失败工作流
            result = self.recovery_handler.process_failed_workflow(
                project_code,
                instance
            )
            results.append(result)

            # 更新统计并发送通知
            if result.recovery_executed:
                self.stats.recovery_attempts += 1

                if result.recovery_success:
                    self.stats.successful_recoveries += 1

                    # 更新调度追踪器状态
                    if self.enable_schedule_optimization:
                        self.schedule_tracker.mark_recovered(
                            project_code=project_code,
                            workflow_code=instance.process_definition_code,
                            instance_id=instance.id
                        )

                    # 发送恢复成功通知
                    if self.notification_manager.has_notifiers():
                        message = build_recovery_success_message(
                            result=result,
                            project_name=monitored.config.name
                        )
                        self.notification_manager.send(message)
                        self.logger.debug(f"已发送恢复成功通知: {instance.name}")
                else:
                    # 更新调度追踪器状态（恢复失败，继续监控）
                    if self.enable_schedule_optimization:
                        self.schedule_tracker.mark_failed(
                            project_code=project_code,
                            workflow_code=instance.process_definition_code,
                            instance_id=instance.id
                        )

                    # 发送恢复失败通知
                    if self.notification_manager.has_notifiers():
                        message = build_recovery_failed_message(
                            result=result,
                            project_name=monitored.config.name
                        )
                        self.notification_manager.send(message)
                        self.logger.debug(f"已发送恢复失败通知: {instance.name}")

                # 触发恢复执行回调
                if self._on_recovery_executed:
                    self._on_recovery_executed(result)

            # 恢复操作之间的间隔
            if result.recovery_executed:
                time.sleep(self.config.retry.recovery_interval)

        return results

    def run(self) -> None:
        """
        运行持续监控

        在持续模式下，会按配置的间隔定期检查工作流状态
        """
        self.logger.info("=" * 60)
        self.logger.info("DolphinScheduler 工作流监控器启动")
        self.logger.info("=" * 60)

        # 检查连接
        if not self.client.check_connection():
            self.logger.error("无法连接到 DolphinScheduler API")
            return

        # 解析项目编码
        if not self._resolve_project_codes():
            self.logger.warning("部分项目未找到，请检查配置")

        active_projects = sum(1 for p in self.monitored_projects if p.status == "active")
        if active_projects == 0:
            self.logger.error("没有活跃的项目可监控")
            return

        self.logger.info(f"监控 {active_projects} 个项目")
        self.logger.info(f"检查间隔: {self.config.monitor.check_interval} 秒")
        self.logger.info(f"自动恢复: {'启用' if self.config.retry.auto_recovery else '禁用'}")
        self.logger.info("-" * 60)

        # 设置信号处理
        self._setup_signal_handlers()

        self._running = True
        self._stop_event.clear()

        try:
            if self.config.monitor.continuous_mode:
                self._run_continuous()
            else:
                self._run_single()
        except KeyboardInterrupt:
            self.logger.info("收到中断信号，正在停止...")
        finally:
            self._running = False
            self._print_summary()

    def _run_continuous(self) -> None:
        """持续运行模式"""
        self.logger.info("进入持续监控模式...")

        while not self._stop_event.is_set():
            self.check_once()

            # 等待下一次检查
            self._stop_event.wait(self.config.monitor.check_interval)

    def _run_single(self) -> None:
        """单次运行模式"""
        self.logger.info("执行单次检查...")
        self.check_once()

    def stop(self) -> None:
        """停止监控"""
        self.logger.info("正在停止监控...")
        self._stop_event.set()

    def _setup_signal_handlers(self) -> None:
        """设置信号处理器"""
        def signal_handler(signum, frame):
            self.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _print_summary(self) -> None:
        """打印监控摘要"""
        self.logger.info("=" * 60)
        self.logger.info("监控摘要")
        self.logger.info("=" * 60)
        self.logger.info(f"开始时间: {self.stats.start_time}")
        self.logger.info(f"结束时间: {datetime.now().isoformat()}")
        self.logger.info(f"检查次数: {self.stats.check_count}")
        self.logger.info(f"发现失败工作流实例: {self.stats.failed_workflows_found}")
        self.logger.info(f"因超过阈值跳过: {self.stats.skipped_due_to_threshold}")
        self.logger.info(f"恢复尝试: {self.stats.recovery_attempts}")
        self.logger.info(f"恢复成功: {self.stats.successful_recoveries}")

        # 输出 API 优化统计
        if self.enable_schedule_optimization:
            self.logger.info("-" * 60)
            self.logger.info("API 调用优化统计:")
            self.logger.info(f"  因调度状态跳过检查: {self.stats.skipped_due_to_schedule} 次")
            self.logger.info(f"  节省 API 调用: {self.stats.api_calls_saved} 次")
            if self.stats.check_count > 0:
                # 估算优化效果
                avg_saved_per_check = self.stats.api_calls_saved / self.stats.check_count
                self.logger.info(f"  平均每次检查节省: {avg_saved_per_check:.1f} 次 API 调用")

        # 输出24小时内失败工作流统计
        if self.stats.workflow_failure_stats:
            self.logger.info("-" * 60)
            self.logger.info("24小时内失败工作流统计（按项目和工作流分组）:")
            for project_name, workflows in self.stats.workflow_failure_stats.items():
                self.logger.info(f"  项目: {project_name}")
                for workflow_name, count in workflows.items():
                    self.logger.info(f"    - {workflow_name}: {count} 个失败实例")

        if self.stats.errors:
            self.logger.info("-" * 60)
            self.logger.info(f"错误数量: {len(self.stats.errors)}")

        # 输出 API 调用统计
        self.logger.info("")
        self.client.print_stats()

        # 输出调度追踪统计
        if self.enable_schedule_optimization:
            self.schedule_tracker.print_stats()

        self.logger.info("=" * 60)

    def get_status(self) -> Dict:
        """获取监控状态"""
        return {
            'running': self._running,
            'stats': {
                'start_time': self.stats.start_time,
                'check_count': self.stats.check_count,
                'failed_workflows_found': self.stats.failed_workflows_found,
                'skipped_due_to_threshold': self.stats.skipped_due_to_threshold,
                'recovery_attempts': self.stats.recovery_attempts,
                'successful_recoveries': self.stats.successful_recoveries,
                'last_check_time': self.stats.last_check_time,
                'workflow_failure_stats': self.stats.workflow_failure_stats
            },
            'monitored_projects': [
                {
                    'name': p.config.name,
                    'status': p.status,
                    'project_code': p.project_code,
                    'last_check_time': p.last_check_time
                }
                for p in self.monitored_projects
            ],
            'config': {
                'check_interval': self.config.monitor.check_interval,
                'continuous_mode': self.config.monitor.continuous_mode,
                'auto_recovery': self.config.retry.auto_recovery,
                'max_recovery_attempts': self.config.retry.max_recovery_attempts
            }
        }


def create_monitor(
    client: DolphinSchedulerClient,
    validator: TaskValidator,
    recovery_handler: RecoveryHandler,
    config: Config
) -> WorkflowMonitor:
    """
    创建工作流监控器

    Args:
        client: DolphinScheduler API 客户端
        validator: 任务验证器
        recovery_handler: 恢复处理器
        config: 配置

    Returns:
        工作流监控器实例
    """
    return WorkflowMonitor(client, validator, recovery_handler, config)
