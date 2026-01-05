"""
邮件通知器

通过 SMTP 发送邮件通知
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from typing import List, Optional

from .base import Notifier, NotificationMessage, NotificationLevel
from ..logger import get_logger


class EmailNotifier(Notifier):
    """邮件通知器"""

    # 级别对应的颜色
    LEVEL_COLOR = {
        NotificationLevel.INFO: "#1890ff",
        NotificationLevel.WARNING: "#faad14",
        NotificationLevel.ERROR: "#f5222d",
        NotificationLevel.SUCCESS: "#52c41a"
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
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addrs: List[str],
        enabled: bool = True,
        use_ssl: bool = True
    ):
        """
        初始化邮件通知器

        Args:
            smtp_host: SMTP 服务器地址
            smtp_port: SMTP 服务器端口
            username: 用户名
            password: 密码
            from_addr: 发件人地址
            to_addrs: 收件人地址列表
            enabled: 是否启用
            use_ssl: 是否使用 SSL/TLS
        """
        super().__init__(enabled)
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.use_ssl = use_ssl
        self.logger = get_logger()

    def get_name(self) -> str:
        """获取通知器名称"""
        return "Email"

    def _format_html_message(self, message: NotificationMessage) -> str:
        """
        格式化为 HTML 邮件

        Args:
            message: 通知消息

        Returns:
            HTML 格式的邮件内容
        """
        emoji = self.LEVEL_EMOJI.get(message.level, "📢")
        color = self.LEVEL_COLOR.get(message.level, "#1890ff")

        # 构建基本信息表格
        info_rows = []

        info_items = [
            ("级别", message.level.value),
            ("时间", message.timestamp),
        ]

        if message.project_name:
            info_items.append(("项目", message.project_name))

        if message.workflow_name:
            info_items.append(("工作流", message.workflow_name))

        if message.workflow_id:
            info_items.append(("工作流ID", str(message.workflow_id)))

        if message.start_time:
            info_items.append(("启动时间", message.start_time))

        for label, value in info_items:
            info_rows.append(
                f"<tr><td style='padding: 8px; border: 1px solid #ddd; font-weight: bold; "
                f"background-color: #f5f5f5;'>{label}</td>"
                f"<td style='padding: 8px; border: 1px solid #ddd;'>{value}</td></tr>"
            )

        info_table = "\n".join(info_rows)

        # 构建额外信息表格
        extra_table = ""
        if message.extra_fields:
            extra_rows = []
            for key, value in message.extra_fields.items():
                extra_rows.append(
                    f"<tr><td style='padding: 8px; border: 1px solid #ddd; font-weight: bold; "
                    f"background-color: #f5f5f5;'>{key}</td>"
                    f"<td style='padding: 8px; border: 1px solid #ddd;'>{value}</td></tr>"
                )
            extra_table = f"""
            <h3 style="color: #333; margin-top: 20px;">详细信息</h3>
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                {''.join(extra_rows)}
            </table>
            """

        # HTML 模板
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px;">
            <div style="border-left: 4px solid {color}; padding-left: 20px; margin-bottom: 20px;">
                <h1 style="color: {color}; margin: 0; font-size: 24px;">{emoji} {message.title}</h1>
            </div>

            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                {info_table}
            </table>

            <div style="background-color: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; padding: 15px; margin: 20px 0;">
                <h3 style="color: #333; margin-top: 0;">消息内容</h3>
                <p style="margin: 0; white-space: pre-wrap;">{message.content}</p>
            </div>

            {extra_table}

            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; color: #999; font-size: 12px;">
                <p>此邮件由 DolphinScheduler 工作流监控器自动发送，请勿回复。</p>
            </div>
        </body>
        </html>
        """

        return html

    def send(self, message: NotificationMessage) -> bool:
        """
        发送邮件通知

        Args:
            message: 通知消息

        Returns:
            是否发送成功
        """
        if not self.enabled:
            return False

        try:
            # 创建邮件对象
            msg = MIMEMultipart('alternative')
            msg['From'] = Header(f"DolphinScheduler 监控器 <{self.from_addr}>")
            msg['To'] = Header(", ".join(self.to_addrs))
            msg['Subject'] = Header(message.title, 'utf-8')

            # 添加 HTML 内容
            html_content = self._format_html_message(message)
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)

            # 连接 SMTP 服务器并发送
            if self.use_ssl:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
                server.starttls()

            server.login(self.username, self.password)
            server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            server.quit()

            self.logger.debug(f"邮件通知发送成功: {message.title}")
            return True

        except Exception as e:
            self.logger.error(f"发送邮件通知时出错: {str(e)}")
            return False
