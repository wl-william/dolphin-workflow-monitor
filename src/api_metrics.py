"""
API 调用监控和统计

记录 API 调用次数、耗时等指标
"""

import time
from typing import Dict, List, Any
from dataclasses import dataclass, field
from threading import Lock
from functools import wraps
from collections import defaultdict


@dataclass
class APIMetric:
    """API 调用指标"""
    call_count: int = 0
    total_duration: float = 0.0
    min_duration: float = float('inf')
    max_duration: float = 0.0
    error_count: int = 0
    last_call_time: float = 0.0

    @property
    def avg_duration(self) -> float:
        """平均耗时（毫秒）"""
        if self.call_count == 0:
            return 0.0
        return self.total_duration / self.call_count

    def record_call(self, duration: float, is_error: bool = False) -> None:
        """
        记录一次调用

        Args:
            duration: 耗时（秒）
            is_error: 是否错误
        """
        self.call_count += 1
        duration_ms = duration * 1000  # 转换为毫秒
        self.total_duration += duration_ms
        self.min_duration = min(self.min_duration, duration_ms)
        self.max_duration = max(self.max_duration, duration_ms)
        self.last_call_time = time.time()

        if is_error:
            self.error_count += 1

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'call_count': self.call_count,
            'error_count': self.error_count,
            'error_rate': f"{self.error_count / self.call_count * 100:.2f}%" if self.call_count > 0 else "0%",
            'avg_duration_ms': f"{self.avg_duration:.2f}",
            'min_duration_ms': f"{self.min_duration:.2f}" if self.min_duration != float('inf') else "0",
            'max_duration_ms': f"{self.max_duration:.2f}",
        }


class APIMetricsCollector:
    """API 指标收集器"""

    def __init__(self):
        """初始化收集器"""
        self._metrics: Dict[str, APIMetric] = defaultdict(APIMetric)
        self._lock = Lock()

    def record_call(self, api_name: str, duration: float, is_error: bool = False) -> None:
        """
        记录 API 调用

        Args:
            api_name: API 名称
            duration: 耗时（秒）
            is_error: 是否错误
        """
        with self._lock:
            self._metrics[api_name].record_call(duration, is_error)

    def get_metric(self, api_name: str) -> APIMetric:
        """
        获取指定 API 的指标

        Args:
            api_name: API 名称

        Returns:
            API 指标
        """
        with self._lock:
            return self._metrics.get(api_name, APIMetric())

    def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有 API 的指标

        Returns:
            API 指标字典
        """
        with self._lock:
            return {
                api_name: metric.to_dict()
                for api_name, metric in self._metrics.items()
            }

    def get_summary(self) -> Dict[str, Any]:
        """
        获取统计摘要

        Returns:
            统计摘要
        """
        with self._lock:
            total_calls = sum(m.call_count for m in self._metrics.values())
            total_errors = sum(m.error_count for m in self._metrics.values())
            total_duration = sum(m.total_duration for m in self._metrics.values())

            # 找出最慢的 API
            slowest_api = None
            slowest_duration = 0.0
            for api_name, metric in self._metrics.items():
                if metric.avg_duration > slowest_duration:
                    slowest_duration = metric.avg_duration
                    slowest_api = api_name

            # 找出调用最频繁的 API
            most_called_api = None
            most_calls = 0
            for api_name, metric in self._metrics.items():
                if metric.call_count > most_calls:
                    most_calls = metric.call_count
                    most_called_api = api_name

            return {
                'total_api_calls': total_calls,
                'total_errors': total_errors,
                'total_duration_ms': f"{total_duration:.2f}",
                'avg_duration_ms': f"{total_duration / total_calls:.2f}" if total_calls > 0 else "0",
                'error_rate': f"{total_errors / total_calls * 100:.2f}%" if total_calls > 0 else "0%",
                'slowest_api': {
                    'name': slowest_api,
                    'avg_duration_ms': f"{slowest_duration:.2f}"
                } if slowest_api else None,
                'most_called_api': {
                    'name': most_called_api,
                    'call_count': most_calls
                } if most_called_api else None,
                'api_count': len(self._metrics)
            }

    def reset(self) -> None:
        """重置所有统计"""
        with self._lock:
            self._metrics.clear()


def monitored(api_name: str = None):
    """
    API 监控装饰器

    Args:
        api_name: API 名称，如果为 None 则使用函数名

    Usage:
        @monitored(api_name="get_projects")
        def get_projects(self):
            # ...
    """
    def decorator(func):
        name = api_name or func.__name__

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            start_time = time.time()
            is_error = False

            try:
                result = func(self, *args, **kwargs)
                return result
            except Exception as e:
                is_error = True
                raise
            finally:
                duration = time.time() - start_time

                # 记录指标
                if hasattr(self, '_metrics_collector'):
                    self._metrics_collector.record_call(name, duration, is_error)

        return wrapper
    return decorator
