"""
钉钉通知器

通过钉钉机器人 Webhook 发送通知
"""

import requests
import hmac
import hashlib
import base64
import time
from urllib.parse import quote_plus
from typing import Optional

from .base import Notifier, NotificationMessage, NotificationLevel
from ..logger import get_logger


class DingTalkNotifier(Notifier):
    """钉钉机器人通知器"""

    # 级别对应的emoji
    LEVEL_EMOJI = {
        NotificationLevel.INFO: "ℹ️",
        NotificationLevel.WARNING: "⚠️",
        NotificationLevel.ERROR: "❌",
        NotificationLevel.SUCCESS: "✅"
    }

    # 级别对应的颜色文字
    LEVEL_TEXT = {
        NotificationLevel.INFO: "信息",
        NotificationLevel.WARNING: "警告",
        NotificationLevel.ERROR: "错误",
        NotificationLevel.SUCCESS: "成功"
    }

    def __init__(
        self,
        webhook_url: str,
        secret: Optional[str] = None,
        enabled: bool = True,
        at_mobiles: Optional[list] = None,
        at_all: bool = False
    ):
        """
        初始化钉钉通知器

        Args:
            webhook_url: 钉钉机器人 Webhook URL
            secret: 钉钉机器人加签密钥（可选）
            enabled: 是否启用
            at_mobiles: 要@的手机号列表（可选）
            at_all: 是否@所有人（可选）
        """
        super().__init__(enabled)
        self.webhook_url = webhook_url
        self.secret = secret
        self.at_mobiles = at_mobiles or []
        self.at_all = at_all
        self.logger = get_logger()

    def get_name(self) -> str:
        """获取通知器名称"""
        return "DingTalk"

    def _generate_sign(self, timestamp: int) -> str:
        """
        生成签名

        Args:
            timestamp: 时间戳（毫秒）

        Returns:
            签名字符串
        """
        if not self.secret:
            return ""

        secret_enc = self.secret.encode('utf-8')
        string_to_sign = f'{timestamp}\n{self.secret}'
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(
            secret_enc,
            string_to_sign_enc,
            digestmod=hashlib.sha256
        ).digest()
        sign = quote_plus(base64.b64encode(hmac_code))
        return sign

    def _build_url(self) -> str:
        """
        构建请求URL（带签名）

        Returns:
            完整的 Webhook URL
        """
        url = self.webhook_url

        if self.secret:
            timestamp = int(time.time() * 1000)
            sign = self._generate_sign(timestamp)
            url = f"{url}&timestamp={timestamp}&sign={sign}"

        return url

    def _format_markdown_message(self, message: NotificationMessage) -> str:
        """
        格式化为 Markdown 消息

        Args:
            message: 通知消息

        Returns:
            Markdown 格式的消息文本
        """
        emoji = self.LEVEL_EMOJI.get(message.level, "📢")
        level_text = self.LEVEL_TEXT.get(message.level, "通知")

        # 构建消息内容
        lines = [
            f"## {emoji} {message.title}",
            "",
            f"**级别**: {level_text}",
            f"**时间**: {message.timestamp}",
        ]

        # 添加项目和工作流信息
        if message.project_name:
            lines.append(f"**项目**: {message.project_name}")

        if message.workflow_name:
            lines.append(f"**工作流**: {message.workflow_name}")

        if message.workflow_id:
            lines.append(f"**工作流ID**: {message.workflow_id}")

        if message.start_time:
            lines.append(f"**启动时间**: {message.start_time}")

        # 添加主要内容
        lines.extend([
            "",
            "---",
            "",
            message.content
        ])

        # 添加额外字段
        if message.extra_fields:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("**详细信息**:")
            for key, value in message.extra_fields.items():
                lines.append(f"- **{key}**: {value}")

        return "\n".join(lines)

    def send(self, message: NotificationMessage) -> bool:
        """
        发送钉钉通知

        Args:
            message: 通知消息

        Returns:
            是否发送成功
        """
        if not self.enabled:
            return False

        try:
            url = self._build_url()
            markdown_text = self._format_markdown_message(message)

            # 构建请求数据
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": message.title,
                    "text": markdown_text
                }
            }

            # 添加@信息
            if self.at_mobiles or self.at_all:
                data["at"] = {
                    "atMobiles": self.at_mobiles,
                    "isAtAll": self.at_all
                }

            # 发送请求
            response = requests.post(
                url,
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )

            # 检查响应
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    self.logger.debug(f"钉钉通知发送成功: {message.title}")
                    return True
                else:
                    self.logger.error(
                        f"钉钉通知发送失败: {result.get('errmsg', '未知错误')}"
                    )
                    return False
            else:
                self.logger.error(
                    f"钉钉通知请求失败: HTTP {response.status_code}"
                )
                return False

        except Exception as e:
            self.logger.error(f"发送钉钉通知时出错: {str(e)}")
            return False
