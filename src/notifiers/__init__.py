"""
通知器模块

支持多种通知渠道：钉钉、企业微信、邮件等
"""

from .base import (
    NotificationMessage,
    NotificationLevel,
    Notifier,
    NotificationManager
)
from .dingtalk import DingTalkNotifier
from .wework import WeWorkNotifier
from .email import EmailNotifier
from .factory import create_notification_manager

__all__ = [
    'NotificationMessage',
    'NotificationLevel',
    'Notifier',
    'NotificationManager',
    'DingTalkNotifier',
    'WeWorkNotifier',
    'EmailNotifier',
    'create_notification_manager',
]
