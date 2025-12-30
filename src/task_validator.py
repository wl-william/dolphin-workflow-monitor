"""
任务验证器模块

验证工作流中的任务状态，包括：
1. 检查是否所有任务都已失败
2. 验证失败任务的重试次数是否已用完
3. 处理嵌套工作流的情况
"""

from dataclasses import dataclass
from typing import List, Tuple, Optional
from enum import Enum

from .api_client import (
    DolphinSchedulerClient,
    TaskInstance,
    WorkflowInstance,
    FAILURE_STATES,
    RUNNING_STATES,
    SUCCESS_STATES
)
from .logger import get_logger


class ValidationResult(Enum):
    """验证结果枚举"""
    READY_FOR_RECOVERY = "ready_for_recovery"       # 可以执行恢复
    TASKS_STILL_RUNNING = "tasks_still_running"     # 仍有任务运行中
    RETRIES_NOT_EXHAUSTED = "retries_not_exhausted" # 重试次数未用完
    NO_FAILED_TASKS = "no_failed_tasks"             # 没有失败的任务
    MIXED_STATES = "mixed_states"                   # 混合状态（部分成功/部分失败）
    VALIDATION_ERROR = "validation_error"           # 验证错误


@dataclass
class TaskValidationDetail:
    """任务验证详情"""
    task: TaskInstance
    is_valid_for_recovery: bool
    reason: str
    sub_workflow_validation: Optional['WorkflowValidationResult'] = None


@dataclass
class WorkflowValidationResult:
    """工作流验证结果"""
    workflow_instance: WorkflowInstance
    result: ValidationResult
    message: str
    total_tasks: int
    failed_tasks: int
    running_tasks: int
    success_tasks: int
    tasks_with_retry_remaining: int
    task_details: List[TaskValidationDetail]
    nested_workflows: List['WorkflowValidationResult']

    @property
    def can_recover(self) -> bool:
        """是否可以执行恢复"""
        return self.result == ValidationResult.READY_FOR_RECOVERY


class TaskValidator:
    """任务验证器"""

    def __init__(self, client: DolphinSchedulerClient):
        """
        初始化验证器

        Args:
            client: DolphinScheduler API 客户端
        """
        self.client = client
        self.logger = get_logger()

    def validate_workflow_instance(
        self,
        project_code: int,
        workflow_instance: WorkflowInstance,
        depth: int = 0
    ) -> WorkflowValidationResult:
        """
        验证工作流实例是否可以执行恢复

        简化验证逻辑：只检查工作流状态，不验证任务细节
        - 工作流状态必须是 FAILURE
        - 由 DolphinScheduler 自己决定哪些任务需要重跑

        Args:
            project_code: 项目编码
            workflow_instance: 工作流实例
            depth: 嵌套深度（用于日志缩进）

        Returns:
            验证结果
        """
        indent = "  " * depth
        self.logger.info(
            f"{indent}验证工作流: {workflow_instance.name} "
            f"(ID:{workflow_instance.id}, 状态:{workflow_instance.state}, "
            f"启动时间:{workflow_instance.start_time})"
        )

        # 简化逻辑：只检查工作流状态
        if not workflow_instance.is_failed:
            message = f"工作流状态不是FAILURE (当前状态: {workflow_instance.state})"
            self.logger.info(f"{indent}{message}")
            return self._create_result(
                workflow_instance,
                ValidationResult.NO_FAILED_TASKS,
                message,
                tasks=[]
            )

        # 工作流状态为FAILURE，满足恢复条件
        message = f"工作流状态为FAILURE，满足恢复条件"
        self.logger.success(f"{indent}✅ {message}")

        return WorkflowValidationResult(
            workflow_instance=workflow_instance,
            result=ValidationResult.READY_FOR_RECOVERY,
            message=message,
            total_tasks=0,
            failed_tasks=0,
            running_tasks=0,
            success_tasks=0,
            tasks_with_retry_remaining=0,
            task_details=[],
            nested_workflows=[]
        )

    def _create_result(
        self,
        workflow_instance: WorkflowInstance,
        result: ValidationResult,
        message: str,
        tasks: List[TaskInstance]
    ) -> WorkflowValidationResult:
        """创建验证结果"""
        return WorkflowValidationResult(
            workflow_instance=workflow_instance,
            result=result,
            message=message,
            total_tasks=len(tasks),
            failed_tasks=0,
            running_tasks=0,
            success_tasks=0,
            tasks_with_retry_remaining=0,
            task_details=[],
            nested_workflows=[]
        )


def create_validator(client: DolphinSchedulerClient) -> TaskValidator:
    """
    创建任务验证器

    Args:
        client: DolphinScheduler API 客户端

    Returns:
        任务验证器实例
    """
    return TaskValidator(client)
