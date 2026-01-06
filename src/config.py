"""
配置管理模块

支持从配置文件和环境变量加载配置
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class ProjectConfig:
    """项目配置"""
    name: str
    workflows: List[str] = field(default_factory=list)
    monitor_all: bool = True


@dataclass
class MonitorConfig:
    """监控配置"""
    check_interval: int = 60
    continuous_mode: bool = True
    timeout: int = 300
    time_window_hours: int = 24  # 只监控指定小时内启动的工作流
    max_failures_for_recovery: int = 1  # 时间窗口内最多失败数量，超过则只通知不恢复


@dataclass
class RetryConfig:
    """重试配置"""
    max_recovery_attempts: int = 3
    recovery_interval: int = 30
    auto_recovery: bool = True


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"
    file: str = "logs/monitor.log"
    max_size: int = 10
    backup_count: int = 5


@dataclass
class DolphinConfig:
    """DolphinScheduler 配置"""
    api_url: str = "http://localhost:12345/dolphinscheduler"
    token: str = ""


@dataclass
class DingTalkConfig:
    """钉钉通知配置"""
    enabled: bool = False
    webhook_url: str = ""
    secret: str = ""
    keyword: str = ""  # 安全设置中的关键词，消息内容需包含此关键词
    at_mobiles: List[str] = field(default_factory=list)
    at_all: bool = False


@dataclass
class WeWorkConfig:
    """企业微信通知配置"""
    enabled: bool = False
    webhook_url: str = ""
    mentioned_list: List[str] = field(default_factory=list)
    mentioned_mobile_list: List[str] = field(default_factory=list)


@dataclass
class EmailConfig:
    """邮件通知配置"""
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: List[str] = field(default_factory=list)
    use_ssl: bool = True


@dataclass
class NotificationConfig:
    """通知配置"""
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)
    wework: WeWorkConfig = field(default_factory=WeWorkConfig)
    email: EmailConfig = field(default_factory=EmailConfig)


class Config:
    """配置管理器"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置

        Args:
            config_path: 配置文件路径，如果为空则使用默认路径
        """
        # 加载环境变量
        load_dotenv()

        # 确定配置文件路径
        if config_path is None:
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / "config" / "config.yaml"
        else:
            config_path = Path(config_path)

        self.config_path = config_path
        self._raw_config: Dict[str, Any] = {}

        # 加载配置
        self._load_config()

        # 解析配置
        self.dolphin = self._parse_dolphin_config()
        self.monitor = self._parse_monitor_config()
        self.retry = self._parse_retry_config()
        self.logging = self._parse_logging_config()
        self.notification = self._parse_notification_config()
        self.projects = self._parse_projects_config()

    def _load_config(self) -> None:
        """加载配置文件"""
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._raw_config = yaml.safe_load(f) or {}
        else:
            self._raw_config = {}

    def _get_env(self, key: str, default: Any = None) -> Any:
        """获取环境变量"""
        return os.environ.get(key, default)

    def _parse_dolphin_config(self) -> DolphinConfig:
        """解析 DolphinScheduler 配置"""
        ds_config = self._raw_config.get('dolphinscheduler', {})

        return DolphinConfig(
            api_url=self._get_env('DS_API_URL', ds_config.get('api_url', 'http://localhost:12345/dolphinscheduler')),
            token=self._get_env('DS_TOKEN', ds_config.get('token', ''))
        )

    def _parse_monitor_config(self) -> MonitorConfig:
        """解析监控配置"""
        mon_config = self._raw_config.get('monitor', {})

        check_interval = self._get_env('DS_CHECK_INTERVAL')
        continuous_mode = self._get_env('DS_CONTINUOUS_MODE')
        time_window_hours = self._get_env('DS_TIME_WINDOW_HOURS')
        max_failures = self._get_env('DS_MAX_FAILURES_FOR_RECOVERY')

        return MonitorConfig(
            check_interval=int(check_interval) if check_interval else mon_config.get('check_interval', 60),
            continuous_mode=continuous_mode.lower() == 'true' if continuous_mode else mon_config.get('continuous_mode', True),
            timeout=mon_config.get('timeout', 300),
            time_window_hours=int(time_window_hours) if time_window_hours else mon_config.get('time_window_hours', 24),
            max_failures_for_recovery=int(max_failures) if max_failures else mon_config.get('max_failures_for_recovery', 1)
        )

    def _parse_retry_config(self) -> RetryConfig:
        """解析重试配置"""
        retry_config = self._raw_config.get('retry', {})

        max_recovery = self._get_env('DS_MAX_RECOVERY_ATTEMPTS')
        auto_recovery = self._get_env('DS_AUTO_RECOVERY')

        return RetryConfig(
            max_recovery_attempts=int(max_recovery) if max_recovery else retry_config.get('max_recovery_attempts', 3),
            recovery_interval=retry_config.get('recovery_interval', 30),
            auto_recovery=auto_recovery.lower() == 'true' if auto_recovery else retry_config.get('auto_recovery', True)
        )

    def _parse_logging_config(self) -> LoggingConfig:
        """解析日志配置"""
        log_config = self._raw_config.get('logging', {})

        return LoggingConfig(
            level=self._get_env('DS_LOG_LEVEL', log_config.get('level', 'INFO')),
            file=log_config.get('file', 'logs/monitor.log'),
            max_size=log_config.get('max_size', 10),
            backup_count=log_config.get('backup_count', 5)
        )

    def _parse_notification_config(self) -> NotificationConfig:
        """解析通知配置"""
        notification_config = self._raw_config.get('notification', {})

        # 钉钉配置
        dingtalk_config = notification_config.get('dingtalk', {})
        dingtalk = DingTalkConfig(
            enabled=self._get_env('DS_DINGTALK_ENABLED', dingtalk_config.get('enabled', False)) in [True, 'true', '1'],
            webhook_url=self._get_env('DS_DINGTALK_WEBHOOK', dingtalk_config.get('webhook_url', '')),
            secret=self._get_env('DS_DINGTALK_SECRET', dingtalk_config.get('secret', '')),
            keyword=self._get_env('DS_DINGTALK_KEYWORD', dingtalk_config.get('keyword', '')),
            at_mobiles=dingtalk_config.get('at_mobiles', []),
            at_all=dingtalk_config.get('at_all', False)
        )

        # 企业微信配置
        wework_config = notification_config.get('wework', {})
        wework = WeWorkConfig(
            enabled=self._get_env('DS_WEWORK_ENABLED', wework_config.get('enabled', False)) in [True, 'true', '1'],
            webhook_url=self._get_env('DS_WEWORK_WEBHOOK', wework_config.get('webhook_url', '')),
            mentioned_list=wework_config.get('mentioned_list', []),
            mentioned_mobile_list=wework_config.get('mentioned_mobile_list', [])
        )

        # 邮件配置
        email_config = notification_config.get('email', {})
        email = EmailConfig(
            enabled=self._get_env('DS_EMAIL_ENABLED', email_config.get('enabled', False)) in [True, 'true', '1'],
            smtp_host=self._get_env('DS_EMAIL_SMTP_HOST', email_config.get('smtp_host', '')),
            smtp_port=int(self._get_env('DS_EMAIL_SMTP_PORT', email_config.get('smtp_port', 465))),
            username=self._get_env('DS_EMAIL_USERNAME', email_config.get('username', '')),
            password=self._get_env('DS_EMAIL_PASSWORD', email_config.get('password', '')),
            from_addr=self._get_env('DS_EMAIL_FROM', email_config.get('from_addr', '')),
            to_addrs=email_config.get('to_addrs', []),
            use_ssl=email_config.get('use_ssl', True)
        )

        return NotificationConfig(
            dingtalk=dingtalk,
            wework=wework,
            email=email
        )

    def _parse_projects_config(self) -> List[ProjectConfig]:
        """解析项目配置"""
        projects_config = self._raw_config.get('projects', {})
        projects = []

        for project_name, project_data in projects_config.items():
            if project_data is None:
                project_data = {}

            projects.append(ProjectConfig(
                name=project_name,
                workflows=project_data.get('workflows', []),
                monitor_all=project_data.get('monitor_all', True)
            ))

        return projects

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'dolphinscheduler': {
                'api_url': self.dolphin.api_url,
                'token': '***' if self.dolphin.token else ''
            },
            'monitor': {
                'check_interval': self.monitor.check_interval,
                'continuous_mode': self.monitor.continuous_mode,
                'timeout': self.monitor.timeout,
                'time_window_hours': self.monitor.time_window_hours,
                'max_failures_for_recovery': self.monitor.max_failures_for_recovery
            },
            'retry': {
                'max_recovery_attempts': self.retry.max_recovery_attempts,
                'recovery_interval': self.retry.recovery_interval,
                'auto_recovery': self.retry.auto_recovery
            },
            'logging': {
                'level': self.logging.level,
                'file': self.logging.file
            },
            'notification': {
                'dingtalk': {
                    'enabled': self.notification.dingtalk.enabled,
                    'webhook_url': '***' if self.notification.dingtalk.webhook_url else '',
                    'secret': '***' if self.notification.dingtalk.secret else ''
                },
                'wework': {
                    'enabled': self.notification.wework.enabled,
                    'webhook_url': '***' if self.notification.wework.webhook_url else ''
                },
                'email': {
                    'enabled': self.notification.email.enabled,
                    'smtp_host': self.notification.email.smtp_host,
                    'smtp_port': self.notification.email.smtp_port,
                    'from_addr': self.notification.email.from_addr,
                    'to_addrs': self.notification.email.to_addrs
                }
            },
            'projects': [
                {
                    'name': p.name,
                    'workflows': p.workflows,
                    'monitor_all': p.monitor_all
                }
                for p in self.projects
            ]
        }


def load_config(config_path: Optional[str] = None) -> Config:
    """
    加载配置

    Args:
        config_path: 配置文件路径

    Returns:
        配置对象
    """
    return Config(config_path)
