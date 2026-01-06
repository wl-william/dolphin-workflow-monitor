"""
通知器工厂模块

根据配置创建和初始化通知器
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import NotificationConfig

from .base import NotificationManager
from .dingtalk import DingTalkNotifier
from .wework import WeWorkNotifier
from .email import EmailNotifier
from ..logger import get_logger


def create_notification_manager(config: 'NotificationConfig') -> NotificationManager:
    """
    根据配置创建通知管理器

    Args:
        config: 通知配置

    Returns:
        初始化好的通知管理器
    """
    logger = get_logger()
    manager = NotificationManager()

    # 钉钉通知
    if config.dingtalk.enabled:
        if config.dingtalk.webhook_url:
            dingtalk = DingTalkNotifier(
                webhook_url=config.dingtalk.webhook_url,
                secret=config.dingtalk.secret or None,
                keyword=config.dingtalk.keyword or None,
                enabled=True,
                at_mobiles=config.dingtalk.at_mobiles,
                at_all=config.dingtalk.at_all
            )
            manager.add_notifier(dingtalk)
            logger.info("钉钉通知已启用")
        else:
            logger.warning("钉钉通知已启用但未配置 webhook_url，已跳过")

    # 企业微信通知
    if config.wework.enabled:
        if config.wework.webhook_url:
            wework = WeWorkNotifier(
                webhook_url=config.wework.webhook_url,
                enabled=True,
                mentioned_list=config.wework.mentioned_list,
                mentioned_mobile_list=config.wework.mentioned_mobile_list
            )
            manager.add_notifier(wework)
            logger.info("企业微信通知已启用")
        else:
            logger.warning("企业微信通知已启用但未配置 webhook_url，已跳过")

    # 邮件通知
    if config.email.enabled:
        if all([config.email.smtp_host, config.email.username,
                config.email.password, config.email.from_addr,
                config.email.to_addrs]):
            email = EmailNotifier(
                smtp_host=config.email.smtp_host,
                smtp_port=config.email.smtp_port,
                username=config.email.username,
                password=config.email.password,
                from_addr=config.email.from_addr,
                to_addrs=config.email.to_addrs,
                enabled=True,
                use_ssl=config.email.use_ssl
            )
            manager.add_notifier(email)
            logger.info("邮件通知已启用")
        else:
            logger.warning("邮件通知已启用但配置不完整，已跳过")

    if manager.has_notifiers():
        logger.info(f"通知系统初始化完成，已启用 {manager.get_notifiers_count()} 个通知渠道")
    else:
        logger.info("未启用任何通知渠道")

    return manager
