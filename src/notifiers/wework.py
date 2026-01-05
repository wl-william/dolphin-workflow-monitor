"""
企业微信通知器

通过企业微信机器人 Webhook 发送通知
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional

from .base import Notifier, NotificationMessage, NotificationLevel
from ..logger import get_logger


def create_session_with_retry(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (429, 500, 502, 503, 504)
) -> requests.Session:
    """
    创建带重试机制的 Session

    Args:
        retries: 最大重试次数
        backoff_factor: 重试间隔因子
        status_forcelist: 需要重试的状态码

    Returns:
        配置好的 Session
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class WeWorkNotifier(Notifier):
    """企业微信机器人通知器"""

    # 级别对应的颜色
    LEVEL_COLOR = {
        NotificationLevel.INFO: "info",
        NotificationLevel.WARNING: "warning",
        NotificationLevel.ERROR: "warning",  # 企业微信没有error样式，用warning
        NotificationLevel.SUCCESS: "info"
    }

    # 级别对应的emoji
    LEVEL_EMOJI = {
        NotificationLevel.INFO: "ℹ️",
        NotificationLevel.WARNING: "⚠️",
        NotificationLevel.ERROR: "❌",
        NotificationLevel.SUCCESS: "✅"
    }

    def __init__(
        self,
        webhook_url: str,
        enabled: bool = True,
        mentioned_list: Optional[list] = None,
        mentioned_mobile_list: Optional[list] = None
    ):
        """
        初始化企业微信通知器

        Args:
            webhook_url: 企业微信机器人 Webhook URL
            enabled: 是否启用
            mentioned_list: 要@的用户ID列表（可选）
            mentioned_mobile_list: 要@的手机号列表（可选）
        """
        super().__init__(enabled)
        self.webhook_url = webhook_url
        self.mentioned_list = mentioned_list or []
        self.mentioned_mobile_list = mentioned_mobile_list or []
        self.logger = get_logger()
        # 创建带重试机制的 Session
        self.session = create_session_with_retry(retries=3, backoff_factor=1.0)

    def get_name(self) -> str:
        """获取通知器名称"""
        return "WeWork"

    def _format_markdown_message(self, message: NotificationMessage) -> str:
        """
        格式化为 Markdown 消息

        Args:
            message: 通知消息

        Returns:
            Markdown 格式的消息文本
        """
        emoji = self.LEVEL_EMOJI.get(message.level, "📢")

        # 构建消息内容
        lines = [
            f"## {emoji} {message.title}",
            "",
            f"> 级别: <font color=\"comment\">{message.level.value}</font>",
            f"> 时间: <font color=\"comment\">{message.timestamp}</font>",
        ]

        # 添加项目和工作流信息
        if message.project_name:
            lines.append(f"> 项目: <font color=\"comment\">{message.project_name}</font>")

        if message.workflow_name:
            lines.append(f"> 工作流: <font color=\"comment\">{message.workflow_name}</font>")

        if message.workflow_id:
            lines.append(f"> 工作流ID: <font color=\"comment\">{message.workflow_id}</font>")

        if message.start_time:
            lines.append(f"> 启动时间: <font color=\"comment\">{message.start_time}</font>")

        # 添加主要内容
        lines.extend([
            "",
            message.content
        ])

        # 添加额外字段
        if message.extra_fields:
            lines.append("")
            lines.append("**详细信息**:")
            for key, value in message.extra_fields.items():
                lines.append(f"> {key}: <font color=\"comment\">{value}</font>")

        return "\n".join(lines)

    def send(self, message: NotificationMessage) -> bool:
        """
        发送企业微信通知

        Args:
            message: 通知消息

        Returns:
            是否发送成功
        """
        if not self.enabled:
            return False

        try:
            markdown_text = self._format_markdown_message(message)

            # 构建请求数据
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "content": markdown_text
                }
            }

            # 发送请求（使用带重试机制的 Session）
            response = self.session.post(
                self.webhook_url,
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=30  # DNS 解析可能较慢，增加超时时间
            )

            # 检查响应
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    self.logger.debug(f"企业微信通知发送成功: {message.title}")
                    return True
                else:
                    self.logger.error(
                        f"企业微信通知发送失败: {result.get('errmsg', '未知错误')}"
                    )
                    return False
            else:
                self.logger.error(
                    f"企业微信通知请求失败: HTTP {response.status_code}"
                )
                return False

        except requests.exceptions.ConnectionError as e:
            self.logger.error(
                f"企业微信通知连接失败 (DNS解析或网络问题): {str(e)}\n"
                f"提示: 请检查 Docker 容器的 DNS 配置，确保可以解析 qyapi.weixin.qq.com"
            )
            return False
        except requests.exceptions.Timeout as e:
            self.logger.error(f"企业微信通知请求超时: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"发送企业微信通知时出错: {str(e)}")
            return False
