"""
Cron 表达式解析器

解析 cron 表达式，计算调度时间
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass
import re


@dataclass
class SchedulePeriod:
    """调度周期"""
    current_start: datetime      # 当前周期开始时间（上次应该执行的时间）
    current_end: datetime        # 当前周期结束时间（下次执行时间）
    next_start: datetime         # 下个周期开始时间
    is_in_execution_window: bool # 当前是否在执行窗口内


class CronParser:
    """
    Cron 表达式解析器

    支持 DolphinScheduler 的 cron 格式:
    秒 分 时 日 月 周 [年]

    示例:
    - "0 0 2 * * ?" - 每天 02:00:00
    - "0 30 1 * * ?" - 每天 01:30:00
    - "0 0 */2 * * ?" - 每2小时
    """

    def __init__(self, cron_expression: str):
        """
        初始化解析器

        Args:
            cron_expression: Cron 表达式
        """
        self.expression = cron_expression
        self.parts = self._parse_expression(cron_expression)

    def _parse_expression(self, expression: str) -> dict:
        """解析 cron 表达式"""
        parts = expression.strip().split()

        # DolphinScheduler 使用 6-7 位 cron (秒 分 时 日 月 周 [年])
        if len(parts) < 6:
            raise ValueError(f"Invalid cron expression: {expression}")

        return {
            'second': parts[0],
            'minute': parts[1],
            'hour': parts[2],
            'day': parts[3],
            'month': parts[4],
            'weekday': parts[5],
            'year': parts[6] if len(parts) > 6 else '*'
        }

    def _parse_field(self, field: str, min_val: int, max_val: int) -> list:
        """
        解析单个 cron 字段，返回所有匹配的值

        支持:
        - * : 所有值
        - n : 具体值
        - n,m : 多个值
        - n-m : 范围
        - */n : 步进
        - ? : 任意（用于日和周）
        """
        if field == '*' or field == '?':
            return list(range(min_val, max_val + 1))

        values = set()

        for part in field.split(','):
            if '/' in part:
                # 步进: */n 或 n/m
                base, step = part.split('/')
                step = int(step)
                if base == '*':
                    start = min_val
                else:
                    start = int(base)
                for v in range(start, max_val + 1, step):
                    values.add(v)
            elif '-' in part:
                # 范围: n-m
                start, end = part.split('-')
                for v in range(int(start), int(end) + 1):
                    values.add(v)
            else:
                # 具体值
                values.add(int(part))

        return sorted(values)

    def get_schedule_times(self, reference_time: datetime = None) -> Tuple[datetime, datetime]:
        """
        获取上次和下次调度时间

        Args:
            reference_time: 参考时间，默认为当前时间

        Returns:
            (上次调度时间, 下次调度时间)
        """
        if reference_time is None:
            reference_time = datetime.now()

        # 解析各字段
        hours = self._parse_field(self.parts['hour'], 0, 23)
        minutes = self._parse_field(self.parts['minute'], 0, 59)
        seconds = self._parse_field(self.parts['second'], 0, 59)

        # 简化处理：假设是每天固定时间执行
        # 取第一个匹配的时间点
        schedule_hour = hours[0] if hours else 0
        schedule_minute = minutes[0] if minutes else 0
        schedule_second = seconds[0] if seconds else 0

        # 计算今天的调度时间
        today_schedule = reference_time.replace(
            hour=schedule_hour,
            minute=schedule_minute,
            second=schedule_second,
            microsecond=0
        )

        # 判断上次和下次调度时间
        if reference_time >= today_schedule:
            # 今天的调度时间已过
            last_schedule = today_schedule
            next_schedule = today_schedule + timedelta(days=1)
        else:
            # 今天的调度时间未到
            last_schedule = today_schedule - timedelta(days=1)
            next_schedule = today_schedule

        return last_schedule, next_schedule

    def get_schedule_period(
        self,
        reference_time: datetime = None,
        execution_window_hours: int = 4
    ) -> SchedulePeriod:
        """
        获取当前调度周期信息

        Args:
            reference_time: 参考时间
            execution_window_hours: 执行窗口时长（小时），在此窗口内需要监控

        Returns:
            调度周期信息
        """
        if reference_time is None:
            reference_time = datetime.now()

        last_schedule, next_schedule = self.get_schedule_times(reference_time)

        # 执行窗口结束时间
        window_end = last_schedule + timedelta(hours=execution_window_hours)

        # 判断是否在执行窗口内
        is_in_window = last_schedule <= reference_time <= window_end

        return SchedulePeriod(
            current_start=last_schedule,
            current_end=next_schedule,
            next_start=next_schedule,
            is_in_execution_window=is_in_window
        )

    def should_monitor_now(
        self,
        reference_time: datetime = None,
        execution_window_hours: int = 4
    ) -> Tuple[bool, str]:
        """
        判断当前是否需要监控

        Args:
            reference_time: 参考时间
            execution_window_hours: 执行窗口时长

        Returns:
            (是否需要监控, 原因说明)
        """
        period = self.get_schedule_period(reference_time, execution_window_hours)

        if period.is_in_execution_window:
            return True, f"在执行窗口内 ({period.current_start.strftime('%H:%M')} - {(period.current_start + timedelta(hours=execution_window_hours)).strftime('%H:%M')})"
        else:
            return False, f"不在执行窗口内，下次调度: {period.next_start.strftime('%Y-%m-%d %H:%M')}"


def parse_cron(expression: str) -> CronParser:
    """
    解析 cron 表达式

    Args:
        expression: Cron 表达式

    Returns:
        CronParser 实例
    """
    return CronParser(expression)


# 简化的 cron 表达式检测
def detect_schedule_type(cron_expression: str) -> str:
    """
    检测调度类型

    Returns:
        'daily', 'hourly', 'weekly', 'monthly', 'custom'
    """
    parts = cron_expression.strip().split()
    if len(parts) < 6:
        return 'custom'

    hour = parts[2]
    day = parts[3]
    month = parts[4]
    weekday = parts[5]

    # 每天固定时间
    if day == '*' and month == '*' and (weekday == '?' or weekday == '*'):
        if '*' not in hour and '/' not in hour:
            return 'daily'
        elif '/' in hour:
            return 'hourly'

    # 每周固定时间
    if day == '?' and weekday not in ['*', '?']:
        return 'weekly'

    # 每月固定时间
    if day not in ['*', '?'] and weekday == '?':
        return 'monthly'

    return 'custom'
