"""
通知器基础模块

定义通知消息格式和通知器接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime


class NotificationLevel(Enum):
    """通知级别"""
    INFO = "info"          # 信息
    WARNING = "warning"    # 警告
    ERROR = "error"        # 错误
    SUCCESS = "success"    # 成功


@dataclass
class NotificationMessage:
    """通知消息"""
    title: str                                    # 标题
    level: NotificationLevel                      # 级别
    content: str                                  # 内容
    workflow_name: Optional[str] = None          # 工作流名称
    workflow_id: Optional[int] = None            # 工作流ID
    project_name: Optional[str] = None           # 项目名称
    start_time: Optional[str] = None             # 启动时间
    extra_fields: Dict[str, Any] = field(default_factory=dict)  # 额外字段
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'title': self.title,
            'level': self.level.value,
            'content': self.content,
            'workflow_name': self.workflow_name,
            'workflow_id': self.workflow_id,
            'project_name': self.project_name,
            'start_time': self.start_time,
            'timestamp': self.timestamp,
            **self.extra_fields
        }


class Notifier(ABC):
    """通知器抽象基类"""

    def __init__(self, enabled: bool = True):
        """
        初始化通知器

        Args:
            enabled: 是否启用
        """
        self.enabled = enabled

    @abstractmethod
    def send(self, message: NotificationMessage) -> bool:
        """
        发送通知

        Args:
            message: 通知消息

        Returns:
            是否发送成功
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """获取通知器名称"""
        pass

    def is_enabled(self) -> bool:
        """是否已启用"""
        return self.enabled


class NotificationManager:
    """通知管理器"""

    def __init__(self):
        """初始化通知管理器"""
        self.notifiers: List[Notifier] = []

    def add_notifier(self, notifier: Notifier) -> None:
        """
        添加通知器

        Args:
            notifier: 通知器实例
        """
        if notifier.is_enabled():
            self.notifiers.append(notifier)

    def send(self, message: NotificationMessage) -> Dict[str, bool]:
        """
        发送通知到所有已启用的通知器

        Args:
            message: 通知消息

        Returns:
            各通知器的发送结果 {notifier_name: success}
        """
        results = {}
        for notifier in self.notifiers:
            try:
                success = notifier.send(message)
                results[notifier.get_name()] = success
            except Exception as e:
                # 通知失败不应该影响主流程
                results[notifier.get_name()] = False
                # 可以记录日志，但不抛出异常

        return results

    def has_notifiers(self) -> bool:
        """是否有已启用的通知器"""
        return len(self.notifiers) > 0

    def get_notifiers_count(self) -> int:
        """获取通知器数量"""
        return len(self.notifiers)
