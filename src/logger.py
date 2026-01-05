"""
日志模块

提供统一的日志记录功能
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

from colorama import init, Fore, Style

# 初始化 colorama
init(autoreset=True)


class ColoredFormatter(logging.Formatter):
    """彩色日志格式化器"""

    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT
    }

    def format(self, record):
        # 为控制台输出添加颜色
        color = self.COLORS.get(record.levelno, '')
        record.levelname = f"{color}{record.levelname}{Style.RESET_ALL}"
        return super().format(record)


class Logger:
    """日志管理器"""

    _instance: Optional['Logger'] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        name: str = "dolphin-monitor",
        level: str = "INFO",
        log_file: Optional[str] = None,
        max_size: int = 10,
        backup_count: int = 5
    ):
        """
        初始化日志管理器

        Args:
            name: 日志名称
            level: 日志级别
            log_file: 日志文件路径
            max_size: 日志文件最大大小（MB）
            backup_count: 保留的日志文件数量
        """
        if self._initialized:
            return

        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self.logger.handlers.clear()

        # 日志格式
        log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"

        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(ColoredFormatter(log_format, date_format))
        self.logger.addHandler(console_handler)

        # 文件处理器
        if log_file:
            try:
                log_path = Path(log_file)

                # 确保日志目录存在
                log_path.parent.mkdir(parents=True, exist_ok=True)

                file_handler = RotatingFileHandler(
                    log_path,
                    maxBytes=max_size * 1024 * 1024,
                    backupCount=backup_count,
                    encoding='utf-8'
                )
                file_handler.setFormatter(logging.Formatter(log_format, date_format))
                self.logger.addHandler(file_handler)
            except (PermissionError, OSError) as e:
                # 如果无法创建日志文件，只输出到控制台
                self.logger.warning(
                    f"无法创建日志文件 {log_file}: {str(e)}. "
                    f"日志将只输出到控制台。"
                )
                self.logger.warning(
                    "提示: 在 Docker 环境中，请确保日志目录有正确的权限。"
                    "参考文档: DOCKER_DEPLOYMENT.md"
                )

        self._initialized = True

    def debug(self, message: str) -> None:
        """记录调试信息"""
        self.logger.debug(message)

    def info(self, message: str) -> None:
        """记录信息"""
        self.logger.info(message)

    def warning(self, message: str) -> None:
        """记录警告"""
        self.logger.warning(message)

    def error(self, message: str) -> None:
        """记录错误"""
        self.logger.error(message)

    def critical(self, message: str) -> None:
        """记录严重错误"""
        self.logger.critical(message)

    def success(self, message: str) -> None:
        """记录成功信息（使用 INFO 级别，但带绿色标记）"""
        self.logger.info(f"✓ {message}")

    def failure(self, message: str) -> None:
        """记录失败信息（使用 ERROR 级别）"""
        self.logger.error(f"✗ {message}")


# 全局日志实例
_logger: Optional[Logger] = None


def setup_logger(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_size: int = 10,
    backup_count: int = 5
) -> Logger:
    """
    设置全局日志

    Args:
        level: 日志级别
        log_file: 日志文件路径
        max_size: 日志文件最大大小（MB）
        backup_count: 保留的日志文件数量

    Returns:
        日志实例
    """
    global _logger
    _logger = Logger(
        level=level,
        log_file=log_file,
        max_size=max_size,
        backup_count=backup_count
    )
    return _logger


def get_logger() -> Logger:
    """获取全局日志实例"""
    global _logger
    if _logger is None:
        _logger = Logger()
    return _logger
