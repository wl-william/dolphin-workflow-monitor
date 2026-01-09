"""
调度状态追踪器

追踪工作流的调度状态，实现智能监控决策
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from enum import Enum

from .cron_parser import CronParser, SchedulePeriod
from .logger import get_logger


class WorkflowPeriodStatus(Enum):
    """工作流周期状态"""
    PENDING = "pending"           # 等待执行（调度时间未到）
    WAITING = "waiting"           # 等待触发（调度时间已到，等待实例创建）
    RUNNING = "running"           # 运行中
    SUCCESS = "success"           # 本周期已成功
    FAILED = "failed"             # 本周期失败，需要监控
    RECOVERED = "recovered"       # 已恢复


@dataclass
class WorkflowScheduleState:
    """工作流调度状态"""
    project_code: int
    project_name: str
    workflow_code: int
    workflow_name: str
    cron_expression: str

    # 当前周期信息
    current_period_start: Optional[str] = None   # 当前周期开始时间
    current_period_end: Optional[str] = None     # 当前周期结束时间
    status: str = WorkflowPeriodStatus.PENDING.value

    # 最近实例信息
    last_instance_id: Optional[int] = None
    last_instance_status: Optional[str] = None
    last_check_time: Optional[str] = None

    # 成功/失败时间
    success_time: Optional[str] = None
    failure_time: Optional[str] = None
    recovery_time: Optional[str] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'WorkflowScheduleState':
        """从字典创建"""
        return cls(**data)


@dataclass
class MonitorDecision:
    """监控决策"""
    should_monitor: bool          # 是否需要监控
    should_query_api: bool        # 是否需要调用 API
    reason: str                   # 决策原因
    workflow_code: int
    workflow_name: str
    current_status: str


class ScheduleTracker:
    """
    调度状态追踪器

    功能：
    1. 追踪每个工作流的调度状态
    2. 根据调度时间决定是否需要监控
    3. 记录成功/失败状态避免重复查询
    """

    def __init__(
        self,
        state_file: str = "data/schedule_state.json",
        execution_window_hours: int = 4,
        success_cooldown_minutes: int = 30
    ):
        """
        初始化追踪器

        Args:
            state_file: 状态文件路径
            execution_window_hours: 执行窗口时长（小时）
            success_cooldown_minutes: 成功后冷却时间（分钟）
        """
        self.state_file = Path(state_file)
        self.execution_window_hours = execution_window_hours
        self.success_cooldown_minutes = success_cooldown_minutes
        self.logger = get_logger()
        self._lock = Lock()

        # 工作流状态: {project_code}_{workflow_code} -> WorkflowScheduleState
        self._states: Dict[str, WorkflowScheduleState] = {}

        # 加载持久化状态
        self._load_state()

    def _get_key(self, project_code: int, workflow_code: int) -> str:
        """生成状态键"""
        return f"{project_code}_{workflow_code}"

    def _load_state(self) -> None:
        """加载持久化状态"""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for key, state_data in data.get('states', {}).items():
                    self._states[key] = WorkflowScheduleState.from_dict(state_data)
            self.logger.debug(f"加载调度状态: {len(self._states)} 个工作流")
        except Exception as e:
            self.logger.warning(f"加载调度状态失败: {e}")

    def _save_state(self) -> None:
        """保存状态到文件"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'updated_at': datetime.now().isoformat(),
                'states': {
                    key: state.to_dict()
                    for key, state in self._states.items()
                }
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"保存调度状态失败: {e}")

    def register_workflow(
        self,
        project_code: int,
        project_name: str,
        workflow_code: int,
        workflow_name: str,
        cron_expression: str
    ) -> None:
        """
        注册工作流调度信息

        Args:
            project_code: 项目编码
            project_name: 项目名称
            workflow_code: 工作流编码
            workflow_name: 工作流名称
            cron_expression: Cron 表达式
        """
        key = self._get_key(project_code, workflow_code)

        with self._lock:
            if key not in self._states:
                self._states[key] = WorkflowScheduleState(
                    project_code=project_code,
                    project_name=project_name,
                    workflow_code=workflow_code,
                    workflow_name=workflow_name,
                    cron_expression=cron_expression
                )
                self.logger.debug(
                    f"注册工作流调度: {workflow_name} ({cron_expression})"
                )
            else:
                # 更新 cron 表达式（可能变更）
                self._states[key].cron_expression = cron_expression

    def update_period(self, project_code: int, workflow_code: int) -> None:
        """
        更新工作流的周期信息

        Args:
            project_code: 项目编码
            workflow_code: 工作流编码
        """
        key = self._get_key(project_code, workflow_code)

        with self._lock:
            if key not in self._states:
                return

            state = self._states[key]
            try:
                parser = CronParser(state.cron_expression)
                period = parser.get_schedule_period(
                    execution_window_hours=self.execution_window_hours
                )

                # 检查是否进入新周期
                new_period_start = period.current_start.isoformat()
                if state.current_period_start != new_period_start:
                    # 新周期，重置状态
                    state.current_period_start = new_period_start
                    state.current_period_end = period.current_end.isoformat()
                    state.status = WorkflowPeriodStatus.PENDING.value
                    state.last_instance_id = None
                    state.last_instance_status = None
                    state.success_time = None
                    state.failure_time = None
                    state.recovery_time = None
                    self.logger.debug(
                        f"工作流 {state.workflow_name} 进入新周期: {new_period_start}"
                    )

            except Exception as e:
                self.logger.warning(
                    f"更新周期信息失败 ({state.workflow_name}): {e}"
                )

    def mark_success(
        self,
        project_code: int,
        workflow_code: int,
        instance_id: int
    ) -> None:
        """
        标记工作流本周期执行成功

        Args:
            project_code: 项目编码
            workflow_code: 工作流编码
            instance_id: 实例 ID
        """
        key = self._get_key(project_code, workflow_code)

        with self._lock:
            if key in self._states:
                state = self._states[key]
                state.status = WorkflowPeriodStatus.SUCCESS.value
                state.success_time = datetime.now().isoformat()
                state.last_instance_id = instance_id
                state.last_instance_status = "SUCCESS"
                state.last_check_time = datetime.now().isoformat()
                self._save_state()
                self.logger.info(
                    f"工作流 {state.workflow_name} 本周期执行成功，跳过后续监控"
                )

    def mark_failed(
        self,
        project_code: int,
        workflow_code: int,
        instance_id: int
    ) -> None:
        """
        标记工作流本周期执行失败

        Args:
            project_code: 项目编码
            workflow_code: 工作流编码
            instance_id: 实例 ID
        """
        key = self._get_key(project_code, workflow_code)

        with self._lock:
            if key in self._states:
                state = self._states[key]
                state.status = WorkflowPeriodStatus.FAILED.value
                state.failure_time = datetime.now().isoformat()
                state.last_instance_id = instance_id
                state.last_instance_status = "FAILURE"
                state.last_check_time = datetime.now().isoformat()
                self._save_state()

    def mark_recovered(
        self,
        project_code: int,
        workflow_code: int,
        instance_id: int
    ) -> None:
        """
        标记工作流已恢复成功

        Args:
            project_code: 项目编码
            workflow_code: 工作流编码
            instance_id: 实例 ID
        """
        key = self._get_key(project_code, workflow_code)

        with self._lock:
            if key in self._states:
                state = self._states[key]
                state.status = WorkflowPeriodStatus.RECOVERED.value
                state.recovery_time = datetime.now().isoformat()
                state.last_instance_id = instance_id
                state.last_check_time = datetime.now().isoformat()
                self._save_state()
                self.logger.info(
                    f"工作流 {state.workflow_name} 已恢复成功"
                )

    def make_decision(
        self,
        project_code: int,
        workflow_code: int
    ) -> MonitorDecision:
        """
        做出监控决策

        Args:
            project_code: 项目编码
            workflow_code: 工作流编码

        Returns:
            监控决策
        """
        key = self._get_key(project_code, workflow_code)

        with self._lock:
            if key not in self._states:
                return MonitorDecision(
                    should_monitor=True,
                    should_query_api=True,
                    reason="未注册的工作流，执行完整监控",
                    workflow_code=workflow_code,
                    workflow_name="未知",
                    current_status="unknown"
                )

            state = self._states[key]

            # 更新周期信息
            self.update_period(project_code, workflow_code)

            # 获取当前调度周期
            try:
                parser = CronParser(state.cron_expression)
                period = parser.get_schedule_period(
                    execution_window_hours=self.execution_window_hours
                )
            except Exception as e:
                return MonitorDecision(
                    should_monitor=True,
                    should_query_api=True,
                    reason=f"Cron 解析失败: {e}，执行完整监控",
                    workflow_code=workflow_code,
                    workflow_name=state.workflow_name,
                    current_status=state.status
                )

            now = datetime.now()

            # 决策逻辑
            # 1. 本周期已成功 -> 跳过
            if state.status == WorkflowPeriodStatus.SUCCESS.value:
                return MonitorDecision(
                    should_monitor=False,
                    should_query_api=False,
                    reason=f"本周期已成功 ({state.success_time})，跳过监控",
                    workflow_code=workflow_code,
                    workflow_name=state.workflow_name,
                    current_status=state.status
                )

            # 2. 本周期已恢复 -> 跳过（短时间内）
            if state.status == WorkflowPeriodStatus.RECOVERED.value:
                if state.recovery_time:
                    recovery_time = datetime.fromisoformat(state.recovery_time)
                    cooldown = timedelta(minutes=self.success_cooldown_minutes)
                    if now - recovery_time < cooldown:
                        remaining = cooldown - (now - recovery_time)
                        return MonitorDecision(
                            should_monitor=False,
                            should_query_api=False,
                            reason=f"刚恢复成功，冷却中 (剩余 {remaining.seconds // 60} 分钟)",
                            workflow_code=workflow_code,
                            workflow_name=state.workflow_name,
                            current_status=state.status
                        )

            # 3. 不在执行窗口内 -> 跳过
            if not period.is_in_execution_window:
                return MonitorDecision(
                    should_monitor=False,
                    should_query_api=False,
                    reason=f"不在执行窗口内，下次调度: {period.next_start.strftime('%Y-%m-%d %H:%M')}",
                    workflow_code=workflow_code,
                    workflow_name=state.workflow_name,
                    current_status=state.status
                )

            # 4. 在执行窗口内且状态为失败 -> 需要监控
            if state.status == WorkflowPeriodStatus.FAILED.value:
                return MonitorDecision(
                    should_monitor=True,
                    should_query_api=True,
                    reason="本周期失败，持续监控",
                    workflow_code=workflow_code,
                    workflow_name=state.workflow_name,
                    current_status=state.status
                )

            # 5. 在执行窗口内，状态待定 -> 需要查询
            return MonitorDecision(
                should_monitor=True,
                should_query_api=True,
                reason="在执行窗口内，检查工作流状态",
                workflow_code=workflow_code,
                workflow_name=state.workflow_name,
                current_status=state.status
            )

    def get_all_decisions(
        self,
        project_code: int,
        workflow_codes: List[int]
    ) -> Dict[int, MonitorDecision]:
        """
        批量获取监控决策

        Args:
            project_code: 项目编码
            workflow_codes: 工作流编码列表

        Returns:
            {workflow_code: MonitorDecision}
        """
        decisions = {}
        for wf_code in workflow_codes:
            decisions[wf_code] = self.make_decision(project_code, wf_code)
        return decisions

    def get_workflows_to_monitor(
        self,
        project_code: int,
        workflow_codes: List[int]
    ) -> Tuple[List[int], List[MonitorDecision]]:
        """
        获取需要监控的工作流列表

        Args:
            project_code: 项目编码
            workflow_codes: 工作流编码列表

        Returns:
            (需要监控的工作流编码列表, 所有决策列表)
        """
        decisions = self.get_all_decisions(project_code, workflow_codes)

        to_monitor = [
            wf_code for wf_code, decision in decisions.items()
            if decision.should_query_api
        ]

        return to_monitor, list(decisions.values())

    def get_stats(self) -> Dict:
        """获取统计信息"""
        with self._lock:
            total = len(self._states)
            by_status = {}
            for state in self._states.values():
                status = state.status
                by_status[status] = by_status.get(status, 0) + 1

            return {
                'total_workflows': total,
                'by_status': by_status,
                'execution_window_hours': self.execution_window_hours,
                'success_cooldown_minutes': self.success_cooldown_minutes
            }

    def print_stats(self) -> None:
        """打印统计信息"""
        stats = self.get_stats()
        self.logger.info("=" * 50)
        self.logger.info("调度追踪统计:")
        self.logger.info(f"  追踪工作流数: {stats['total_workflows']}")
        self.logger.info(f"  执行窗口: {stats['execution_window_hours']} 小时")
        self.logger.info(f"  成功冷却: {stats['success_cooldown_minutes']} 分钟")
        if stats['by_status']:
            self.logger.info("  状态分布:")
            for status, count in stats['by_status'].items():
                self.logger.info(f"    - {status}: {count}")
        self.logger.info("=" * 50)
