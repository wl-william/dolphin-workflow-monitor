"""
恢复处理器模块

负责执行工作流恢复操作，包括：
1. 验证是否满足恢复条件
2. 记录恢复历史
3. 限制恢复次数
4. 执行恢复操作
"""

import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .api_client import DolphinSchedulerClient, WorkflowInstance
from .task_validator import TaskValidator, WorkflowValidationResult, ValidationResult
from .config import RetryConfig
from .logger import get_logger


@dataclass
class RecoveryRecord:
    """恢复记录"""
    workflow_instance_id: int
    workflow_name: str
    project_code: int
    attempt_count: int = 0
    last_attempt_time: Optional[str] = None
    recovery_history: List[Dict] = field(default_factory=list)

    def add_attempt(self, success: bool, message: str) -> None:
        """添加恢复尝试记录"""
        self.attempt_count += 1
        self.last_attempt_time = datetime.now().isoformat()
        self.recovery_history.append({
            'attempt': self.attempt_count,
            'time': self.last_attempt_time,
            'success': success,
            'message': message
        })


@dataclass
class RecoveryResult:
    """恢复结果"""
    workflow_instance: WorkflowInstance
    validation_result: WorkflowValidationResult
    recovery_executed: bool
    recovery_success: bool
    message: str
    attempt_count: int


class RecoveryHandler:
    """恢复处理器"""

    def __init__(
        self,
        client: DolphinSchedulerClient,
        validator: TaskValidator,
        config: RetryConfig,
        state_file: Optional[str] = None
    ):
        """
        初始化恢复处理器

        Args:
            client: DolphinScheduler API 客户端
            validator: 任务验证器
            config: 重试配置
            state_file: 状态文件路径
        """
        self.client = client
        self.validator = validator
        self.config = config
        self.logger = get_logger()

        # 状态文件用于持久化恢复记录
        if state_file is None:
            state_file = Path(__file__).parent.parent / "logs" / "recovery_state.json"
        self.state_file = Path(state_file)

        # 加载恢复记录
        self._recovery_records: Dict[int, RecoveryRecord] = {}
        self._load_state()

    def _load_state(self) -> None:
        """从文件加载恢复状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, value in data.items():
                        self._recovery_records[int(key)] = RecoveryRecord(
                            workflow_instance_id=value['workflow_instance_id'],
                            workflow_name=value['workflow_name'],
                            project_code=value['project_code'],
                            attempt_count=value.get('attempt_count', 0),
                            last_attempt_time=value.get('last_attempt_time'),
                            recovery_history=value.get('recovery_history', [])
                        )
                self.logger.debug(f"加载了 {len(self._recovery_records)} 条恢复记录")
            except Exception as e:
                self.logger.warning(f"加载恢复状态失败: {e}")

    def _save_state(self) -> None:
        """保存恢复状态到文件"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for key, record in self._recovery_records.items():
                data[str(key)] = {
                    'workflow_instance_id': record.workflow_instance_id,
                    'workflow_name': record.workflow_name,
                    'project_code': record.project_code,
                    'attempt_count': record.attempt_count,
                    'last_attempt_time': record.last_attempt_time,
                    'recovery_history': record.recovery_history
                }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"保存恢复状态失败: {e}")

    def _get_recovery_record(
        self,
        workflow_instance: WorkflowInstance
    ) -> RecoveryRecord:
        """获取或创建恢复记录"""
        instance_id = workflow_instance.id

        if instance_id not in self._recovery_records:
            self._recovery_records[instance_id] = RecoveryRecord(
                workflow_instance_id=instance_id,
                workflow_name=workflow_instance.name,
                project_code=workflow_instance.project_code
            )

        return self._recovery_records[instance_id]

    def process_failed_workflow(
        self,
        project_code: int,
        workflow_instance: WorkflowInstance
    ) -> RecoveryResult:
        """
        处理失败的工作流

        执行流程：
        1. 验证工作流是否满足恢复条件
        2. 检查恢复次数限制
        3. 如果启用自动恢复且满足条件，执行恢复
        4. 记录处理结果

        Args:
            project_code: 项目编码
            workflow_instance: 工作流实例

        Returns:
            恢复结果
        """
        self.logger.info(f"处理失败工作流: {workflow_instance.name} (ID: {workflow_instance.id})")

        # 验证工作流
        validation_result = self.validator.validate_workflow_instance(
            project_code,
            workflow_instance
        )

        record = self._get_recovery_record(workflow_instance)

        # 检查是否可以恢复
        if not validation_result.can_recover:
            self.logger.info(
                f"工作流 {workflow_instance.name} 不满足恢复条件: {validation_result.message}"
            )
            return RecoveryResult(
                workflow_instance=workflow_instance,
                validation_result=validation_result,
                recovery_executed=False,
                recovery_success=False,
                message=validation_result.message,
                attempt_count=record.attempt_count
            )

        # 检查恢复次数限制
        if record.attempt_count >= self.config.max_recovery_attempts:
            message = (
                f"工作流 {workflow_instance.name} 已达到最大恢复次数限制 "
                f"({record.attempt_count}/{self.config.max_recovery_attempts})"
            )
            self.logger.warning(message)
            return RecoveryResult(
                workflow_instance=workflow_instance,
                validation_result=validation_result,
                recovery_executed=False,
                recovery_success=False,
                message=message,
                attempt_count=record.attempt_count
            )

        # 检查是否启用自动恢复
        if not self.config.auto_recovery:
            message = f"工作流 {workflow_instance.name} 满足恢复条件，但自动恢复已禁用"
            self.logger.info(message)
            return RecoveryResult(
                workflow_instance=workflow_instance,
                validation_result=validation_result,
                recovery_executed=False,
                recovery_success=False,
                message=message,
                attempt_count=record.attempt_count
            )

        # 执行恢复
        self.logger.info(
            f"开始恢复工作流: {workflow_instance.name} "
            f"(第 {record.attempt_count + 1}/{self.config.max_recovery_attempts} 次尝试)"
        )

        success = self.client.execute_failure_recovery(
            project_code,
            workflow_instance.id
        )

        if success:
            message = f"工作流 {workflow_instance.name} 恢复操作已提交"
            self.logger.success(message)
            record.add_attempt(True, message)
        else:
            message = f"工作流 {workflow_instance.name} 恢复操作失败"
            self.logger.error(message)
            record.add_attempt(False, message)

        # 保存状态
        self._save_state()

        return RecoveryResult(
            workflow_instance=workflow_instance,
            validation_result=validation_result,
            recovery_executed=True,
            recovery_success=success,
            message=message,
            attempt_count=record.attempt_count
        )

    def get_recovery_statistics(self) -> Dict:
        """获取恢复统计信息"""
        total_records = len(self._recovery_records)
        total_attempts = sum(r.attempt_count for r in self._recovery_records.values())
        successful_recoveries = sum(
            1 for r in self._recovery_records.values()
            if any(h.get('success', False) for h in r.recovery_history)
        )

        return {
            'total_workflows_tracked': total_records,
            'total_recovery_attempts': total_attempts,
            'successful_recoveries': successful_recoveries,
            'max_recovery_limit': self.config.max_recovery_attempts,
            'auto_recovery_enabled': self.config.auto_recovery
        }

    def clear_recovery_record(self, workflow_instance_id: int) -> bool:
        """
        清除指定工作流的恢复记录

        Args:
            workflow_instance_id: 工作流实例 ID

        Returns:
            是否成功
        """
        if workflow_instance_id in self._recovery_records:
            del self._recovery_records[workflow_instance_id]
            self._save_state()
            self.logger.info(f"已清除工作流实例 {workflow_instance_id} 的恢复记录")
            return True
        return False

    def clear_all_records(self) -> int:
        """
        清除所有恢复记录

        Returns:
            清除的记录数量
        """
        count = len(self._recovery_records)
        self._recovery_records.clear()
        self._save_state()
        self.logger.info(f"已清除所有恢复记录 (共 {count} 条)")
        return count


def create_recovery_handler(
    client: DolphinSchedulerClient,
    validator: TaskValidator,
    config: RetryConfig,
    state_file: Optional[str] = None
) -> RecoveryHandler:
    """
    创建恢复处理器

    Args:
        client: DolphinScheduler API 客户端
        validator: 任务验证器
        config: 重试配置
        state_file: 状态文件路径

    Returns:
        恢复处理器实例
    """
    return RecoveryHandler(client, validator, config, state_file)
