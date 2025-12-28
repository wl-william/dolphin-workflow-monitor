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

        验证条件：
        1. 工作流中所有任务均已完成（没有运行中的任务）
        2. 存在失败的任务
        3. 所有失败任务的重试次数已用完

        Args:
            project_code: 项目编码
            workflow_instance: 工作流实例
            depth: 嵌套深度（用于日志缩进）

        Returns:
            验证结果
        """
        indent = "  " * depth
        self.logger.debug(f"{indent}开始验证工作流: {workflow_instance.name} (ID: {workflow_instance.id})")

        # 获取所有任务
        tasks = self.client.get_task_instances(project_code, workflow_instance.id)

        if not tasks:
            self.logger.warning(f"{indent}工作流 {workflow_instance.name} 没有任务")
            return self._create_result(
                workflow_instance,
                ValidationResult.VALIDATION_ERROR,
                "工作流没有任务",
                tasks=[]
            )

        # 统计任务状态
        task_details: List[TaskValidationDetail] = []
        nested_results: List[WorkflowValidationResult] = []

        failed_tasks = []
        running_tasks = []
        success_tasks = []
        tasks_with_retry_remaining = []

        for task in tasks:
            detail = self._validate_task(project_code, task, depth)
            task_details.append(detail)

            # 处理嵌套工作流
            if detail.sub_workflow_validation:
                nested_results.append(detail.sub_workflow_validation)

            # 统计状态
            if task.is_failed:
                failed_tasks.append(task)
                if not task.retry_exhausted:
                    tasks_with_retry_remaining.append(task)
            elif task.is_running:
                running_tasks.append(task)
            elif task.is_success:
                success_tasks.append(task)

        # 判断验证结果
        result, message = self._determine_validation_result(
            workflow_instance=workflow_instance,
            tasks=tasks,
            failed_tasks=failed_tasks,
            running_tasks=running_tasks,
            success_tasks=success_tasks,
            tasks_with_retry_remaining=tasks_with_retry_remaining,
            nested_results=nested_results,
            indent=indent
        )

        return WorkflowValidationResult(
            workflow_instance=workflow_instance,
            result=result,
            message=message,
            total_tasks=len(tasks),
            failed_tasks=len(failed_tasks),
            running_tasks=len(running_tasks),
            success_tasks=len(success_tasks),
            tasks_with_retry_remaining=len(tasks_with_retry_remaining),
            task_details=task_details,
            nested_workflows=nested_results
        )

    def _validate_task(
        self,
        project_code: int,
        task: TaskInstance,
        depth: int
    ) -> TaskValidationDetail:
        """
        验证单个任务

        Args:
            project_code: 项目编码
            task: 任务实例
            depth: 嵌套深度

        Returns:
            任务验证详情
        """
        indent = "  " * depth
        sub_workflow_result = None

        # 如果是子工作流类型，需要递归验证
        if task.is_sub_process:
            self.logger.debug(f"{indent}  发现子工作流任务: {task.name}")
            sub_instance = self.client.get_sub_process_instance(project_code, task.id)

            if sub_instance:
                sub_workflow_result = self.validate_workflow_instance(
                    project_code,
                    sub_instance,
                    depth + 1
                )

        # 验证任务状态
        if task.is_running:
            return TaskValidationDetail(
                task=task,
                is_valid_for_recovery=False,
                reason="任务仍在运行中",
                sub_workflow_validation=sub_workflow_result
            )

        if task.is_failed:
            if not task.retry_exhausted:
                return TaskValidationDetail(
                    task=task,
                    is_valid_for_recovery=False,
                    reason=f"重试次数未用完 ({task.retry_times}/{task.max_retry_times})",
                    sub_workflow_validation=sub_workflow_result
                )
            else:
                return TaskValidationDetail(
                    task=task,
                    is_valid_for_recovery=True,
                    reason=f"任务已失败且重试次数已用完 ({task.retry_times}/{task.max_retry_times})",
                    sub_workflow_validation=sub_workflow_result
                )

        if task.is_success:
            return TaskValidationDetail(
                task=task,
                is_valid_for_recovery=True,
                reason="任务已成功",
                sub_workflow_validation=sub_workflow_result
            )

        return TaskValidationDetail(
            task=task,
            is_valid_for_recovery=False,
            reason=f"未知状态: {task.state}",
            sub_workflow_validation=sub_workflow_result
        )

    def _determine_validation_result(
        self,
        workflow_instance: WorkflowInstance,
        tasks: List[TaskInstance],
        failed_tasks: List[TaskInstance],
        running_tasks: List[TaskInstance],
        success_tasks: List[TaskInstance],
        tasks_with_retry_remaining: List[TaskInstance],
        nested_results: List[WorkflowValidationResult],
        indent: str
    ) -> Tuple[ValidationResult, str]:
        """
        根据任务状态统计确定验证结果

        Args:
            workflow_instance: 工作流实例
            tasks: 所有任务
            failed_tasks: 失败的任务
            running_tasks: 运行中的任务
            success_tasks: 成功的任务
            tasks_with_retry_remaining: 重试次数未用完的任务
            nested_results: 嵌套工作流验证结果
            indent: 日志缩进

        Returns:
            (验证结果, 消息)
        """
        workflow_name = workflow_instance.name

        # 检查是否有任务仍在运行
        if running_tasks:
            task_names = ", ".join([t.name for t in running_tasks[:3]])
            if len(running_tasks) > 3:
                task_names += f" 等 {len(running_tasks)} 个任务"
            message = f"工作流 {workflow_name} 中仍有任务在运行: {task_names}"
            self.logger.info(f"{indent}{message}")
            return ValidationResult.TASKS_STILL_RUNNING, message

        # 检查嵌套工作流中是否有运行中的任务
        for nested in nested_results:
            if nested.result == ValidationResult.TASKS_STILL_RUNNING:
                message = f"工作流 {workflow_name} 的子工作流 {nested.workflow_instance.name} 中仍有任务在运行"
                self.logger.info(f"{indent}{message}")
                return ValidationResult.TASKS_STILL_RUNNING, message

        # 检查是否有失败的任务
        if not failed_tasks:
            # 检查嵌套工作流中是否有失败的任务
            has_nested_failures = any(
                nested.failed_tasks > 0 for nested in nested_results
            )
            if not has_nested_failures:
                message = f"工作流 {workflow_name} 中没有失败的任务"
                self.logger.info(f"{indent}{message}")
                return ValidationResult.NO_FAILED_TASKS, message

        # 检查失败任务的重试次数是否已用完
        if tasks_with_retry_remaining:
            task_info = []
            for t in tasks_with_retry_remaining[:3]:
                task_info.append(f"{t.name}({t.retry_times}/{t.max_retry_times})")
            info_str = ", ".join(task_info)
            if len(tasks_with_retry_remaining) > 3:
                info_str += f" 等 {len(tasks_with_retry_remaining)} 个任务"
            message = f"工作流 {workflow_name} 中有任务重试次数未用完: {info_str}"
            self.logger.info(f"{indent}{message}")
            return ValidationResult.RETRIES_NOT_EXHAUSTED, message

        # 检查嵌套工作流中是否有重试次数未用完的任务
        for nested in nested_results:
            if nested.result == ValidationResult.RETRIES_NOT_EXHAUSTED:
                message = f"工作流 {workflow_name} 的子工作流 {nested.workflow_instance.name} 中有任务重试次数未用完"
                self.logger.info(f"{indent}{message}")
                return ValidationResult.RETRIES_NOT_EXHAUSTED, message

        # 所有条件都满足，可以执行恢复
        message = f"工作流 {workflow_name} 满足恢复条件: {len(failed_tasks)} 个失败任务，{len(success_tasks)} 个成功任务"
        self.logger.success(f"{indent}{message}")
        return ValidationResult.READY_FOR_RECOVERY, message

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
