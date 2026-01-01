#!/usr/bin/env python3
"""测试状态判断修复"""

from src.api_client import WorkflowInstance, TaskInstance, WorkflowState, TaskState


def test_workflow_instance_with_string_state():
    """测试字符串格式的状态"""
    print("测试 WorkflowInstance 字符串状态...")

    # 创建一个状态为字符串 "FAILURE" 的工作流实例
    workflow = WorkflowInstance(
        id=335821,
        name="欧洲日任务调度-16-20260101023001237",
        process_definition_code=10215015368288,
        project_code=123456,
        state="FAILURE",  # 字符串格式
        run_times=1,
        start_time="2026-01-01 10:30:01"
    )

    print(f"  状态值: {workflow.state}")
    print(f"  状态类型: {type(workflow.state)}")
    print(f"  is_failed: {workflow.is_failed}")
    print(f"  is_running: {workflow.is_running}")
    print(f"  is_success: {workflow.is_success}")

    assert workflow.is_failed == True, "字符串 'FAILURE' 应该被识别为失败状态"
    assert workflow.is_running == False
    assert workflow.is_success == False
    print("  ✅ 字符串状态测试通过")


def test_workflow_instance_with_int_state():
    """测试整数格式的状态"""
    print("\n测试 WorkflowInstance 整数状态...")

    # 创建一个状态为整数 6 的工作流实例
    workflow = WorkflowInstance(
        id=335821,
        name="欧洲日任务调度-16-20260101023001237",
        process_definition_code=10215015368288,
        project_code=123456,
        state=6,  # 整数格式（WorkflowState.FAILURE.value）
        run_times=1,
        start_time="2026-01-01 10:30:01"
    )

    print(f"  状态值: {workflow.state}")
    print(f"  状态类型: {type(workflow.state)}")
    print(f"  is_failed: {workflow.is_failed}")
    print(f"  is_running: {workflow.is_running}")
    print(f"  is_success: {workflow.is_success}")

    assert workflow.is_failed == True, "整数 6 应该被识别为失败状态"
    assert workflow.is_running == False
    assert workflow.is_success == False
    print("  ✅ 整数状态测试通过")


def test_task_instance_with_string_state():
    """测试任务实例的字符串状态"""
    print("\n测试 TaskInstance 字符串状态...")

    task = TaskInstance(
        id=1,
        name="test_task",
        task_type="SHELL",
        state="FAILURE",  # 字符串格式
        max_retry_times=3,
        retry_times=0,
        process_instance_id=335821
    )

    print(f"  状态值: {task.state}")
    print(f"  状态类型: {type(task.state)}")
    print(f"  is_failed: {task.is_failed}")
    print(f"  is_running: {task.is_running}")
    print(f"  is_success: {task.is_success}")

    assert task.is_failed == True, "字符串 'FAILURE' 应该被识别为失败状态"
    assert task.is_running == False
    assert task.is_success == False
    print("  ✅ TaskInstance 字符串状态测试通过")


def test_case_insensitive():
    """测试大小写不敏感"""
    print("\n测试大小写不敏感...")

    # 测试小写
    workflow1 = WorkflowInstance(
        id=1, name="test", process_definition_code=1,
        project_code=1, state="failure", run_times=1
    )
    assert workflow1.is_failed == True, "小写 'failure' 应该被识别"

    # 测试混合大小写
    workflow2 = WorkflowInstance(
        id=1, name="test", process_definition_code=1,
        project_code=1, state="FaiLure", run_times=1
    )
    assert workflow2.is_failed == True, "混合大小写 'FaiLure' 应该被识别"

    print("  ✅ 大小写不敏感测试通过")


if __name__ == "__main__":
    print("=" * 60)
    print("运行状态判断修复测试")
    print("=" * 60)

    try:
        test_workflow_instance_with_string_state()
        test_workflow_instance_with_int_state()
        test_task_instance_with_string_state()
        test_case_insensitive()

        print("\n" + "=" * 60)
        print("✅ 所有测试通过！修复成功！")
        print("=" * 60)
    except AssertionError as e:
        print("\n" + "=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60)
        exit(1)
