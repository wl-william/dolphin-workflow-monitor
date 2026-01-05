"""
消息构建器模块

为不同场景构建通知消息
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..api_client import WorkflowInstance
    from ..recovery_handler import RecoveryResult

from .base import NotificationMessage, NotificationLevel


def build_failure_detected_message(
    workflow: 'WorkflowInstance',
    project_name: str,
    reason: str = ""
) -> NotificationMessage:
    """
    构建失败检测通知消息

    Args:
        workflow: 工作流实例
        project_name: 项目名称
        reason: 失败原因或额外说明

    Returns:
        通知消息
    """
    title = f"🔍 检测到工作流失败"

    content_lines = [
        f"在项目 **{project_name}** 中检测到工作流失败。",
        "",
        "请关注并检查工作流状态。"
    ]

    if reason:
        content_lines.insert(1, f"原因: {reason}")

    content = "\n".join(content_lines)

    extra_fields = {}
    if workflow.run_times:
        extra_fields["运行次数"] = workflow.run_times

    return NotificationMessage(
        title=title,
        level=NotificationLevel.WARNING,
        content=content,
        workflow_name=workflow.name,
        workflow_id=workflow.id,
        project_name=project_name,
        start_time=workflow.start_time,
        extra_fields=extra_fields
    )


def build_recovery_success_message(
    result: 'RecoveryResult',
    project_name: str
) -> NotificationMessage:
    """
    构建恢复成功通知消息

    Args:
        result: 恢复结果
        project_name: 项目名称

    Returns:
        通知消息
    """
    workflow = result.workflow_instance
    title = f"✅ 工作流恢复成功"

    content = f"工作流已成功从失败节点恢复，正在重新执行。"

    extra_fields = {
        "恢复尝试次数": result.attempt_count,
        "工作流运行次数": workflow.run_times
    }

    return NotificationMessage(
        title=title,
        level=NotificationLevel.SUCCESS,
        content=content,
        workflow_name=workflow.name,
        workflow_id=workflow.id,
        project_name=project_name,
        start_time=workflow.start_time,
        extra_fields=extra_fields
    )


def build_recovery_failed_message(
    result: 'RecoveryResult',
    project_name: str
) -> NotificationMessage:
    """
    构建恢复失败通知消息

    Args:
        result: 恢复结果
        project_name: 项目名称

    Returns:
        通知消息
    """
    workflow = result.workflow_instance
    title = f"❌ 工作流恢复失败"

    content_lines = [
        f"尝试恢复工作流失败，请人工介入处理。",
        ""
    ]

    if result.skip_reason:
        content_lines.append(f"**跳过原因**: {result.skip_reason}")

    content = "\n".join(content_lines)

    extra_fields = {
        "恢复尝试次数": result.attempt_count,
        "工作流运行次数": workflow.run_times
    }

    if result.validation_result:
        extra_fields["验证结果"] = result.validation_result.message

    return NotificationMessage(
        title=title,
        level=NotificationLevel.ERROR,
        content=content,
        workflow_name=workflow.name,
        workflow_id=workflow.id,
        project_name=project_name,
        start_time=workflow.start_time,
        extra_fields=extra_fields
    )


def build_threshold_exceeded_message(
    workflow: 'WorkflowInstance',
    project_name: str,
    failure_count: int,
    threshold: int,
    time_window: int
) -> NotificationMessage:
    """
    构建超过阈值通知消息

    Args:
        workflow: 工作流实例
        project_name: 项目名称
        failure_count: 失败次数
        threshold: 阈值
        time_window: 时间窗口（小时）

    Returns:
        通知消息
    """
    title = f"⚠️ 工作流失败次数超过阈值"

    content = (
        f"工作流在 **{time_window}** 小时内失败了 **{failure_count}** 次，"
        f"超过阈值（{threshold}个）。\n\n"
        f"**已暂停自动恢复**，请人工检查工作流配置或系统状态。"
    )

    extra_fields = {
        "时间窗口（小时）": time_window,
        "失败次数": failure_count,
        "阈值": threshold,
        "工作流运行次数": workflow.run_times
    }

    return NotificationMessage(
        title=title,
        level=NotificationLevel.WARNING,
        content=content,
        workflow_name=workflow.name,
        workflow_id=workflow.id,
        project_name=project_name,
        start_time=workflow.start_time,
        extra_fields=extra_fields
    )
