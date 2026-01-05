"""
API 缓存层

缓存不常变化的数据，减少 API 调用次数
"""

import time
from typing import Dict, Any, Optional, Callable, TypeVar, Generic
from dataclasses import dataclass
from threading import Lock
from functools import wraps

T = TypeVar('T')


@dataclass
class CacheEntry(Generic[T]):
    """缓存条目"""
    value: T
    expire_at: float  # Unix 时间戳

    def is_expired(self) -> bool:
        """是否过期"""
        return time.time() > self.expire_at


class APICache:
    """API 缓存"""

    def __init__(self):
        """初始化缓存"""
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._hit_count = 0
        self._miss_count = 0

    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存值

        Args:
            key: 缓存键

        Returns:
            缓存值，如果不存在或已过期返回 None
        """
        with self._lock:
            if key not in self._cache:
                self._miss_count += 1
                return None

            entry = self._cache[key]
            if entry.is_expired():
                # 过期，删除缓存
                del self._cache[key]
                self._miss_count += 1
                return None

            self._hit_count += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        """
        设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
            ttl_seconds: 过期时间（秒），默认1小时
        """
        with self._lock:
            expire_at = time.time() + ttl_seconds
            self._cache[key] = CacheEntry(value=value, expire_at=expire_at)

    def delete(self, key: str) -> None:
        """
        删除缓存

        Args:
            key: 缓存键
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            self._hit_count = 0
            self._miss_count = 0

    def get_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计

        Returns:
            统计信息字典
        """
        with self._lock:
            total_requests = self._hit_count + self._miss_count
            hit_rate = (
                self._hit_count / total_requests * 100
                if total_requests > 0
                else 0
            )

            return {
                'cache_size': len(self._cache),
                'hit_count': self._hit_count,
                'miss_count': self._miss_count,
                'total_requests': total_requests,
                'hit_rate': f"{hit_rate:.2f}%"
            }

    def clean_expired(self) -> int:
        """
        清理过期缓存

        Returns:
            清理的缓存数量
        """
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]

            for key in expired_keys:
                del self._cache[key]

            return len(expired_keys)


def cached(ttl_seconds: int = 3600, key_prefix: str = ""):
    """
    缓存装饰器

    Args:
        ttl_seconds: 缓存过期时间（秒）
        key_prefix: 缓存键前缀

    Usage:
        @cached(ttl_seconds=3600, key_prefix="projects")
        def get_projects(self):
            # ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # 生成缓存键
            cache_key = f"{key_prefix}:{func.__name__}"
            if args:
                cache_key += f":{':'.join(str(arg) for arg in args)}"
            if kwargs:
                cache_key += f":{':'.join(f'{k}={v}' for k, v in sorted(kwargs.items()))}"

            # 尝试从缓存获取
            if hasattr(self, '_cache'):
                cached_value = self._cache.get(cache_key)
                if cached_value is not None:
                    return cached_value

            # 缓存未命中，调用原函数
            result = func(self, *args, **kwargs)

            # 存入缓存
            if hasattr(self, '_cache') and result is not None:
                self._cache.set(cache_key, result, ttl_seconds)

            return result

        return wrapper
    return decorator
