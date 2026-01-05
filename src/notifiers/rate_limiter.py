"""
通知限流器

防止短时间内对同一个工作流发送过多通知
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from dataclasses import dataclass, field, asdict
from threading import Lock


@dataclass
class NotificationRecord:
    """通知记录"""
    workflow_definition_code: int
    workflow_name: str
    project_name: str
    notification_times: List[str] = field(default_factory=list)  # ISO格式时间戳列表

    def clean_expired(self, time_window_hours: int = 24) -> None:
        """清理过期的通知记录"""
        cutoff_time = datetime.now() - timedelta(hours=time_window_hours)
        self.notification_times = [
            t for t in self.notification_times
            if datetime.fromisoformat(t) > cutoff_time
        ]

    def can_notify(self, max_notifications: int, time_window_hours: int = 24) -> bool:
        """
        判断是否可以发送通知

        Args:
            max_notifications: 时间窗口内最大通知次数
            time_window_hours: 时间窗口（小时）

        Returns:
            是否可以发送通知
        """
        self.clean_expired(time_window_hours)
        return len(self.notification_times) < max_notifications

    def add_notification(self) -> None:
        """记录一次通知"""
        self.notification_times.append(datetime.now().isoformat())

    def get_notification_count(self, time_window_hours: int = 24) -> int:
        """获取时间窗口内的通知次数"""
        self.clean_expired(time_window_hours)
        return len(self.notification_times)


class NotificationRateLimiter:
    """通知限流器"""

    def __init__(
        self,
        state_file: str = "logs/notification_rate_limit.json",
        time_window_hours: int = 24,
        max_notifications: int = 6
    ):
        """
        初始化限流器

        Args:
            state_file: 状态文件路径
            time_window_hours: 时间窗口（小时）
            max_notifications: 时间窗口内最大通知次数
        """
        self.state_file = Path(state_file)
        self.time_window_hours = time_window_hours
        self.max_notifications = max_notifications
        self.records: Dict[str, NotificationRecord] = {}
        self._lock = Lock()

        # 加载历史记录
        self._load_state()

    def _get_key(self, project_name: str, workflow_definition_code: int) -> str:
        """生成记录键"""
        return f"{project_name}:{workflow_definition_code}"

    def _load_state(self) -> None:
        """从文件加载状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, record_data in data.items():
                        self.records[key] = NotificationRecord(**record_data)
            except Exception:
                # 如果加载失败，从空状态开始
                self.records = {}

    def _save_state(self) -> None:
        """保存状态到文件"""
        try:
            # 确保目录存在
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            # 保存记录
            data = {key: asdict(record) for key, record in self.records.items()}
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            # 保存失败不影响主流程
            pass

    def can_notify(
        self,
        project_name: str,
        workflow_definition_code: int,
        workflow_name: str = ""
    ) -> bool:
        """
        判断是否可以发送通知

        Args:
            project_name: 项目名称
            workflow_definition_code: 工作流定义编码
            workflow_name: 工作流名称（可选）

        Returns:
            是否可以发送通知
        """
        with self._lock:
            key = self._get_key(project_name, workflow_definition_code)

            # 如果没有记录，说明可以通知
            if key not in self.records:
                return True

            record = self.records[key]
            return record.can_notify(self.max_notifications, self.time_window_hours)

    def record_notification(
        self,
        project_name: str,
        workflow_definition_code: int,
        workflow_name: str = ""
    ) -> None:
        """
        记录一次通知

        Args:
            project_name: 项目名称
            workflow_definition_code: 工作流定义编码
            workflow_name: 工作流名称（可选）
        """
        with self._lock:
            key = self._get_key(project_name, workflow_definition_code)

            # 如果没有记录，创建新记录
            if key not in self.records:
                self.records[key] = NotificationRecord(
                    workflow_definition_code=workflow_definition_code,
                    workflow_name=workflow_name,
                    project_name=project_name
                )

            # 记录通知
            self.records[key].add_notification()

            # 保存状态
            self._save_state()

    def get_notification_count(
        self,
        project_name: str,
        workflow_definition_code: int
    ) -> int:
        """
        获取时间窗口内的通知次数

        Args:
            project_name: 项目名称
            workflow_definition_code: 工作流定义编码

        Returns:
            通知次数
        """
        with self._lock:
            key = self._get_key(project_name, workflow_definition_code)

            if key not in self.records:
                return 0

            return self.records[key].get_notification_count(self.time_window_hours)

    def get_remaining_notifications(
        self,
        project_name: str,
        workflow_definition_code: int
    ) -> int:
        """
        获取剩余可通知次数

        Args:
            project_name: 项目名称
            workflow_definition_code: 工作流定义编码

        Returns:
            剩余可通知次数
        """
        count = self.get_notification_count(project_name, workflow_definition_code)
        return max(0, self.max_notifications - count)

    def clean_expired_records(self) -> int:
        """
        清理所有过期记录

        Returns:
            清理的记录数
        """
        with self._lock:
            cleaned = 0
            for record in self.records.values():
                before_count = len(record.notification_times)
                record.clean_expired(self.time_window_hours)
                cleaned += before_count - len(record.notification_times)

            # 移除空记录
            empty_keys = [
                key for key, record in self.records.items()
                if len(record.notification_times) == 0
            ]
            for key in empty_keys:
                del self.records[key]

            if cleaned > 0:
                self._save_state()

            return cleaned
