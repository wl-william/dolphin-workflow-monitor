"""
命令行接口模块

提供命令行工具入口
"""

import sys
import json
import click
from pathlib import Path

from .config import load_config
from .logger import setup_logger, get_logger
from .api_client import DolphinSchedulerClient
from .task_validator import TaskValidator
from .recovery_handler import RecoveryHandler
from .monitor import WorkflowMonitor


def init_components(config_path: str = None):
    """初始化所有组件"""
    # 加载配置
    config = load_config(config_path)

    # 设置日志
    base_dir = Path(__file__).parent.parent
    log_file = base_dir / config.logging.file
    setup_logger(
        level=config.logging.level,
        log_file=str(log_file),
        max_size=config.logging.max_size,
        backup_count=config.logging.backup_count
    )

    logger = get_logger()

    # 检查 Token
    if not config.dolphin.token:
        logger.error("未配置 DolphinScheduler Token，请设置环境变量 DS_TOKEN 或在配置文件中配置")
        sys.exit(1)

    # 创建客户端
    client = DolphinSchedulerClient(
        api_url=config.dolphin.api_url,
        token=config.dolphin.token
    )

    # 创建验证器
    validator = TaskValidator(client)

    # 创建恢复处理器
    recovery_handler = RecoveryHandler(
        client=client,
        validator=validator,
        config=config.retry,
        state_file=str(base_dir / "logs" / "recovery_state.json")
    )

    # 创建监控器
    monitor = WorkflowMonitor(
        client=client,
        validator=validator,
        recovery_handler=recovery_handler,
        config=config
    )

    return config, client, validator, recovery_handler, monitor


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """DolphinScheduler 工作流监控器

    自动监控工作流状态，并在满足条件时执行失败恢复。
    """
    pass


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
def run(config):
    """启动监控服务

    持续监控配置的项目和工作流，检测失败状态并执行恢复。
    """
    _, _, _, _, monitor = init_components(config)
    monitor.run()


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
def check(config):
    """执行一次检查

    单次检查所有配置的项目和工作流状态。
    """
    cfg, _, _, _, monitor = init_components(config)

    # 临时设置为单次模式
    cfg.monitor.continuous_mode = False
    monitor.run()


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
def test_connection(config):
    """测试 API 连接

    验证 DolphinScheduler API 连接是否正常。
    """
    cfg, client, _, _, _ = init_components(config)
    logger = get_logger()

    logger.info("测试 API 连接...")
    logger.info(f"API 地址: {cfg.dolphin.api_url}")

    if client.check_connection():
        logger.success("连接成功!")

        # 获取项目列表
        projects = client.get_projects()
        logger.info(f"找到 {len(projects)} 个项目:")
        for p in projects[:10]:
            logger.info(f"  - {p.name} (code: {p.code})")
        if len(projects) > 10:
            logger.info(f"  ... 还有 {len(projects) - 10} 个项目")
    else:
        logger.failure("连接失败!")
        sys.exit(1)


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
@click.option(
    '--project', '-p',
    required=True,
    help='项目名称'
)
@click.option(
    '--workflow', '-w',
    help='工作流名称（可选，不指定则检查所有）'
)
def list_workflows(config, project, workflow):
    """列出工作流状态

    查看指定项目的工作流状态。
    """
    _, client, _, _, _ = init_components(config)
    logger = get_logger()

    # 获取项目
    proj = client.get_project_by_name(project)
    if not proj:
        logger.error(f"未找到项目: {project}")
        sys.exit(1)

    logger.info(f"项目: {proj.name} (code: {proj.code})")

    # 获取工作流定义
    definitions = client.get_process_definitions(proj.code)
    if workflow:
        definitions = [d for d in definitions if d.name == workflow]

    if not definitions:
        logger.info("未找到工作流")
        return

    logger.info(f"找到 {len(definitions)} 个工作流:")

    for defn in definitions:
        logger.info(f"\n工作流: {defn.name}")

        # 获取最近的实例
        instances = client.get_workflow_instances(
            proj.code,
            process_definition_code=defn.code,
            page_size=5
        )

        if instances:
            for inst in instances:
                state = "成功" if inst.is_success else "失败" if inst.is_failed else "运行中" if inst.is_running else "其他"
                logger.info(
                    f"  [{state}] ID: {inst.id} | 开始时间: {inst.start_time} | "
                    f"运行次数: {inst.run_times}"
                )
        else:
            logger.info("  没有执行记录")


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
@click.option(
    '--project', '-p',
    required=True,
    help='项目名称'
)
@click.option(
    '--instance-id', '-i',
    type=int,
    required=True,
    help='工作流实例 ID'
)
def validate_workflow(config, project, instance_id):
    """验证工作流实例

    检查指定工作流实例是否满足恢复条件。
    """
    _, client, validator, _, _ = init_components(config)
    logger = get_logger()

    # 获取项目
    proj = client.get_project_by_name(project)
    if not proj:
        logger.error(f"未找到项目: {project}")
        sys.exit(1)

    # 获取工作流实例
    instances = client.get_workflow_instances(proj.code)
    instance = next((i for i in instances if i.id == instance_id), None)

    if not instance:
        logger.error(f"未找到工作流实例: {instance_id}")
        sys.exit(1)

    logger.info(f"验证工作流实例: {instance.name} (ID: {instance.id})")

    # 执行验证
    result = validator.validate_workflow_instance(proj.code, instance)

    # 输出结果
    logger.info("\n" + "=" * 60)
    logger.info("验证结果")
    logger.info("=" * 60)
    logger.info(f"状态: {result.result.value}")
    logger.info(f"消息: {result.message}")
    logger.info(f"总任务数: {result.total_tasks}")
    logger.info(f"失败任务: {result.failed_tasks}")
    logger.info(f"运行中任务: {result.running_tasks}")
    logger.info(f"成功任务: {result.success_tasks}")
    logger.info(f"重试次数未用完的任务: {result.tasks_with_retry_remaining}")
    logger.info(f"可以恢复: {'是' if result.can_recover else '否'}")

    if result.task_details:
        logger.info("\n任务详情:")
        for detail in result.task_details:
            task = detail.task
            status = "✓" if detail.is_valid_for_recovery else "✗"
            task_type = f" [{task.task_type}]" if task.is_sub_process else ""
            logger.info(
                f"  {status} {task.name}{task_type}: {detail.reason}"
            )

    if result.nested_workflows:
        logger.info("\n嵌套工作流:")
        for nested in result.nested_workflows:
            logger.info(f"  - {nested.workflow_instance.name}: {nested.result.value}")


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
@click.option(
    '--project', '-p',
    required=True,
    help='项目名称'
)
@click.option(
    '--instance-id', '-i',
    type=int,
    required=True,
    help='工作流实例 ID'
)
@click.option(
    '--force', '-f',
    is_flag=True,
    help='强制执行（跳过验证）'
)
def recover(config, project, instance_id, force):
    """手动恢复工作流

    从失败节点恢复执行指定的工作流实例。
    """
    _, client, validator, recovery_handler, _ = init_components(config)
    logger = get_logger()

    # 获取项目
    proj = client.get_project_by_name(project)
    if not proj:
        logger.error(f"未找到项目: {project}")
        sys.exit(1)

    # 获取工作流实例
    instances = client.get_workflow_instances(proj.code)
    instance = next((i for i in instances if i.id == instance_id), None)

    if not instance:
        logger.error(f"未找到工作流实例: {instance_id}")
        sys.exit(1)

    if not instance.is_failed and not force:
        logger.warning(f"工作流实例 {instance_id} 状态不是失败，使用 --force 强制执行")
        sys.exit(1)

    if force:
        logger.warning("强制模式：跳过验证直接执行恢复")
        success = client.execute_failure_recovery(proj.code, instance_id)
        if success:
            logger.success("恢复操作已提交")
        else:
            logger.failure("恢复操作失败")
    else:
        result = recovery_handler.process_failed_workflow(proj.code, instance)
        logger.info(f"恢复结果: {result.message}")


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
def stats(config):
    """查看恢复统计

    显示恢复操作的统计信息。
    """
    _, _, _, recovery_handler, _ = init_components(config)
    logger = get_logger()

    statistics = recovery_handler.get_recovery_statistics()

    logger.info("恢复统计信息")
    logger.info("=" * 40)
    logger.info(f"跟踪的工作流数量: {statistics['total_workflows_tracked']}")
    logger.info(f"总恢复尝试次数: {statistics['total_recovery_attempts']}")
    logger.info(f"成功恢复次数: {statistics['successful_recoveries']}")
    logger.info(f"最大恢复次数限制: {statistics['max_recovery_limit']}")
    logger.info(f"自动恢复: {'启用' if statistics['auto_recovery_enabled'] else '禁用'}")


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
@click.option(
    '--instance-id', '-i',
    type=int,
    help='工作流实例 ID（不指定则清除所有）'
)
@click.confirmation_option(prompt='确定要清除恢复记录吗？')
def clear_records(config, instance_id):
    """清除恢复记录

    清除指定或所有工作流的恢复记录。
    """
    _, _, _, recovery_handler, _ = init_components(config)
    logger = get_logger()

    if instance_id:
        if recovery_handler.clear_recovery_record(instance_id):
            logger.success(f"已清除工作流实例 {instance_id} 的恢复记录")
        else:
            logger.warning(f"未找到工作流实例 {instance_id} 的恢复记录")
    else:
        count = recovery_handler.clear_all_records()
        logger.success(f"已清除 {count} 条恢复记录")


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True),
    help='配置文件路径'
)
def show_config(config):
    """显示当前配置

    显示加载的配置信息。
    """
    cfg = load_config(config)

    click.echo("\n当前配置:")
    click.echo("=" * 60)
    click.echo(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False))


def main():
    """主入口"""
    cli()


if __name__ == '__main__':
    main()
