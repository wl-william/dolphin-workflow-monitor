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


@dataclass
class MonitorStats:
    """监控统计信息"""
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    check_count: int = 0
    failed_workflows_found: int = 0
    recovery_attempts: int = 0
    successful_recoveries: int = 0
    last_check_time: Optional[str] = None
    errors: List[str] = field(default_factory=list)


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

                # 如果不是监控所有工作流，需要解析工作流编码
                if not monitored.config.monitor_all and monitored.config.workflows:
                    workflows = self.client.get_process_definitions(project.code)
                    workflow_map = {w.name: w.code for w in workflows}

                    for wf_name in monitored.config.workflows:
                        if wf_name in workflow_map:
                            monitored.workflow_codes[wf_name] = workflow_map[wf_name]
                            self.logger.debug(f"  - 工作流: {wf_name} (code: {workflow_map[wf_name]})")
                        else:
                            self.logger.warning(f"  - 未找到工作流: {wf_name}")
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
        if monitored.config.monitor_all:
            # 监控所有工作流
            failed_instances = self.client.get_failed_workflow_instances(project_code)
        else:
            # 只监控指定的工作流
            failed_instances = []
            for wf_name, wf_code in monitored.workflow_codes.items():
                instances = self.client.get_failed_workflow_instances(
                    project_code,
                    process_definition_code=wf_code
                )
                failed_instances.extend(instances)

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
            f"项目 {monitored.config.name} 中发现 {len(recent_failed)} 个失败的工作流"
            f"（{time_window_hours}小时内启动）"
        )
        self.stats.failed_workflows_found += len(recent_failed)

        # 处理每个失败的工作流（只处理24小时内的）
        for instance in recent_failed:
            # 触发失败检测回调
            if self._on_failure_detected:
                self._on_failure_detected(instance)

            # 处理失败工作流
            result = self.recovery_handler.process_failed_workflow(
                project_code,
                instance
            )
            results.append(result)

            # 更新统计
            if result.recovery_executed:
                self.stats.recovery_attempts += 1
                if result.recovery_success:
                    self.stats.successful_recoveries += 1

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
        self.logger.info(f"发现失败工作流: {self.stats.failed_workflows_found}")
        self.logger.info(f"恢复尝试: {self.stats.recovery_attempts}")
        self.logger.info(f"恢复成功: {self.stats.successful_recoveries}")
        if self.stats.errors:
            self.logger.info(f"错误数量: {len(self.stats.errors)}")
        self.logger.info("=" * 60)

    def get_status(self) -> Dict:
        """获取监控状态"""
        return {
            'running': self._running,
            'stats': {
                'start_time': self.stats.start_time,
                'check_count': self.stats.check_count,
                'failed_workflows_found': self.stats.failed_workflows_found,
                'recovery_attempts': self.stats.recovery_attempts,
                'successful_recoveries': self.stats.successful_recoveries,
                'last_check_time': self.stats.last_check_time
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
