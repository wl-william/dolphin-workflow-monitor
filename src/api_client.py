"""
DolphinScheduler API 客户端

封装 DolphinScheduler 的 REST API 调用
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum

from .logger import get_logger
from .api_cache import APICache, cached
from .api_metrics import APIMetricsCollector, monitored


class TaskState(Enum):
    """任务状态枚举"""
    SUBMITTED_SUCCESS = 0
    RUNNING_EXECUTION = 1
    READY_PAUSE = 2
    PAUSE = 3
    READY_STOP = 4
    STOP = 5
    FAILURE = 6
    SUCCESS = 7
    NEED_FAULT_TOLERANCE = 8
    KILL = 9
    WAITING_THREAD = 10
    WAITING_DEPEND = 11
    DELAY_EXECUTION = 12
    FORCED_SUCCESS = 13
    SERIAL_WAIT = 14
    DISPATCH = 15
    READY_BLOCK = 16
    BLOCK = 17


class WorkflowState(Enum):
    """工作流状态枚举"""
    SUBMITTED_SUCCESS = 0
    RUNNING_EXECUTION = 1
    READY_PAUSE = 2
    PAUSE = 3
    READY_STOP = 4
    STOP = 5
    FAILURE = 6
    SUCCESS = 7
    NEED_FAULT_TOLERANCE = 8
    KILL = 9
    WAITING_THREAD = 10
    WAITING_DEPEND = 11
    DELAY_EXECUTION = 12
    FORCED_SUCCESS = 13
    SERIAL_WAIT = 14


# 失败状态集合
FAILURE_STATES = {
    TaskState.FAILURE.value,
    TaskState.KILL.value,
    TaskState.NEED_FAULT_TOLERANCE.value
}

# 运行中状态集合
RUNNING_STATES = {
    TaskState.RUNNING_EXECUTION.value,
    TaskState.SUBMITTED_SUCCESS.value,
    TaskState.DELAY_EXECUTION.value,
    TaskState.DISPATCH.value,
    TaskState.WAITING_THREAD.value,
    TaskState.WAITING_DEPEND.value
}

# 成功状态集合
SUCCESS_STATES = {
    TaskState.SUCCESS.value,
    TaskState.FORCED_SUCCESS.value
}


@dataclass
class TaskInstance:
    """任务实例"""
    id: int
    name: str
    task_type: str
    state: int
    max_retry_times: int
    retry_times: int
    process_instance_id: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    @property
    def is_failed(self) -> bool:
        """是否失败"""
        # 支持字符串和整数两种格式
        if isinstance(self.state, str):
            return self.state.upper() in {'FAILURE', 'KILL', 'NEED_FAULT_TOLERANCE'}
        return self.state in FAILURE_STATES

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        # 支持字符串和整数两种格式
        if isinstance(self.state, str):
            return self.state.upper() in {
                'RUNNING_EXECUTION', 'SUBMITTED_SUCCESS', 'DELAY_EXECUTION',
                'DISPATCH', 'WAITING_THREAD', 'WAITING_DEPEND'
            }
        return self.state in RUNNING_STATES

    @property
    def is_success(self) -> bool:
        """是否成功"""
        # 支持字符串和整数两种格式
        if isinstance(self.state, str):
            return self.state.upper() in {'SUCCESS', 'FORCED_SUCCESS'}
        return self.state in SUCCESS_STATES

    @property
    def retry_exhausted(self) -> bool:
        """重试次数是否已用完"""
        return self.retry_times >= self.max_retry_times

    @property
    def is_sub_process(self) -> bool:
        """是否是子工作流（嵌套工作流）"""
        return self.task_type.upper() == 'SUB_PROCESS'


@dataclass
class WorkflowInstance:
    """工作流实例"""
    id: int
    name: str
    process_definition_code: int
    project_code: int
    state: int
    run_times: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    command_type: Optional[str] = None
    recovery: Optional[str] = None

    @property
    def is_failed(self) -> bool:
        """是否失败"""
        # 支持字符串和整数两种格式
        if isinstance(self.state, str):
            return self.state.upper() == 'FAILURE'
        return self.state == WorkflowState.FAILURE.value

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        # 支持字符串和整数两种格式
        if isinstance(self.state, str):
            return self.state.upper() in {'RUNNING_EXECUTION', 'SUBMITTED_SUCCESS'}
        return self.state in {
            WorkflowState.RUNNING_EXECUTION.value,
            WorkflowState.SUBMITTED_SUCCESS.value
        }

    @property
    def is_success(self) -> bool:
        """是否成功"""
        # 支持字符串和整数两种格式
        if isinstance(self.state, str):
            return self.state.upper() == 'SUCCESS'
        return self.state == WorkflowState.SUCCESS.value


@dataclass
class Project:
    """项目"""
    id: int
    code: int
    name: str
    description: Optional[str] = None


@dataclass
class ProcessDefinition:
    """工作流定义"""
    id: int
    code: int
    name: str
    project_code: int
    description: Optional[str] = None


@dataclass
class WorkflowSchedule:
    """工作流调度信息"""
    id: int
    process_definition_code: int
    process_definition_name: str
    project_name: str
    crontab: str                     # Cron 表达式
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    timezone_id: str = "Asia/Shanghai"
    release_state: str = "ONLINE"    # ONLINE/OFFLINE


class DolphinSchedulerClient:
    """DolphinScheduler API 客户端"""

    def __init__(
        self,
        api_url: str,
        token: str,
        enable_cache: bool = True,
        enable_metrics: bool = True,
        max_retries: int = 3,
        pool_connections: int = 10,
        pool_maxsize: int = 20
    ):
        """
        初始化客户端

        Args:
            api_url: API 地址
            token: 认证 Token
            enable_cache: 是否启用缓存
            enable_metrics: 是否启用监控
            max_retries: 最大重试次数
            pool_connections: 连接池大小
            pool_maxsize: 最大连接数
        """
        self.api_url = api_url.rstrip('/')
        self.token = token
        self.logger = get_logger()

        # 初始化缓存和监控
        self._cache = APICache() if enable_cache else None
        self._metrics_collector = APIMetricsCollector() if enable_metrics else None

        # 配置 Session
        self.session = requests.Session()
        self.session.headers.update({
            'token': token,
            'Content-Type': 'application/x-www-form-urlencoded'
        })

        # 配置重试策略
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=0.5,  # 重试间隔：0.5s, 1s, 2s...
            status_forcelist=[429, 500, 502, 503, 504],  # 对这些状态码重试
            allowed_methods=["GET", "POST"]  # 允许重试的方法
        )

        # 配置连接池
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize
        )

        # 挂载适配器
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        发送 API 请求

        Args:
            method: HTTP 方法
            endpoint: API 端点
            params: 查询参数
            data: 请求体数据

        Returns:
            响应数据
        """
        url = f"{self.api_url}{endpoint}"

        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            if result.get('code') != 0:
                self.logger.error(f"API 错误: {result.get('msg', '未知错误')}")
                return {'success': False, 'data': None, 'msg': result.get('msg')}

            return {'success': True, 'data': result.get('data'), 'msg': 'success'}

        except requests.exceptions.RequestException as e:
            self.logger.error(f"请求失败: {str(e)}")
            return {'success': False, 'data': None, 'msg': str(e)}

    @cached(ttl_seconds=3600, key_prefix="projects")
    @monitored(api_name="get_projects")
    def get_projects(self) -> List[Project]:
        """
        获取所有项目（缓存1小时）

        Returns:
            项目列表
        """
        result = self._request('GET', '/projects/list')

        if not result['success'] or not result['data']:
            return []

        return [
            Project(
                id=p.get('id'),
                code=p.get('code'),
                name=p.get('name'),
                description=p.get('description')
            )
            for p in result['data']
        ]

    def get_project_by_name(self, project_name: str) -> Optional[Project]:
        """
        根据名称获取项目

        Args:
            project_name: 项目名称

        Returns:
            项目对象
        """
        projects = self.get_projects()
        for project in projects:
            if project.name == project_name:
                return project
        return None

    @cached(ttl_seconds=3600, key_prefix="process_definitions")
    @monitored(api_name="get_process_definitions")
    def get_process_definitions(
        self,
        project_code: int,
        page_no: int = 1,
        page_size: int = 100
    ) -> List[ProcessDefinition]:
        """
        获取项目下的工作流定义（缓存1小时）

        Args:
            project_code: 项目编码
            page_no: 页码
            page_size: 每页数量

        Returns:
            工作流定义列表
        """
        result = self._request(
            'GET',
            f'/projects/{project_code}/process-definition',
            params={'pageNo': page_no, 'pageSize': page_size}
        )

        if not result['success'] or not result['data']:
            return []

        records = result['data'].get('totalList', [])
        return [
            ProcessDefinition(
                id=p.get('id'),
                code=p.get('code'),
                name=p.get('name'),
                project_code=project_code,
                description=p.get('description')
            )
            for p in records
        ]

    @cached(ttl_seconds=3600, key_prefix="schedules")
    @monitored(api_name="get_workflow_schedules")
    def get_workflow_schedules(
        self,
        project_code: int,
        process_definition_code: Optional[int] = None
    ) -> List[WorkflowSchedule]:
        """
        获取工作流调度信息（缓存1小时）

        Args:
            project_code: 项目编码
            process_definition_code: 工作流定义编码（可选，不传则获取项目下所有调度）

        Returns:
            工作流调度列表
        """
        params = {
            'pageNo': 1,
            'pageSize': 100
        }

        if process_definition_code:
            params['processDefinitionCode'] = process_definition_code

        result = self._request(
            'GET',
            f'/projects/{project_code}/schedules',
            params=params
        )

        if not result['success'] or not result['data']:
            return []

        records = result['data'].get('totalList', [])
        return [
            WorkflowSchedule(
                id=s.get('id'),
                process_definition_code=s.get('processDefinitionCode'),
                process_definition_name=s.get('processDefinitionName', ''),
                project_name=s.get('projectName', ''),
                crontab=s.get('crontab', ''),
                start_time=s.get('startTime'),
                end_time=s.get('endTime'),
                timezone_id=s.get('timezoneId', 'Asia/Shanghai'),
                release_state=s.get('releaseState', 'OFFLINE')
            )
            for s in records
        ]

    def get_workflow_schedule_map(
        self,
        project_code: int
    ) -> Dict[int, WorkflowSchedule]:
        """
        获取项目下所有工作流的调度信息映射

        Args:
            project_code: 项目编码

        Returns:
            {process_definition_code: WorkflowSchedule}
        """
        schedules = self.get_workflow_schedules(project_code)
        return {
            s.process_definition_code: s
            for s in schedules
            if s.release_state == 'ONLINE'  # 只返回已上线的调度
        }

    @monitored(api_name="get_workflow_instances")
    def get_workflow_instances(
        self,
        project_code: int,
        process_definition_code: Optional[int] = None,
        state_type: Optional[str] = None,
        page_no: int = 1,
        page_size: int = 20
    ) -> List[WorkflowInstance]:
        """
        获取工作流实例列表

        Args:
            project_code: 项目编码
            process_definition_code: 工作流定义编码（可选）
            state_type: 状态过滤（可选）
            page_no: 页码
            page_size: 每页数量

        Returns:
            工作流实例列表
        """
        params = {
            'pageNo': page_no,
            'pageSize': page_size
        }

        if process_definition_code:
            params['processDefineCode'] = process_definition_code

        if state_type:
            params['stateType'] = state_type

        result = self._request(
            'GET',
            f'/projects/{project_code}/process-instances',
            params=params
        )

        if not result['success'] or not result['data']:
            return []

        records = result['data'].get('totalList', [])
        return [
            WorkflowInstance(
                id=p.get('id'),
                name=p.get('name'),
                process_definition_code=p.get('processDefinitionCode'),
                project_code=project_code,
                state=p.get('state'),
                run_times=p.get('runTimes', 0),
                start_time=p.get('startTime'),
                end_time=p.get('endTime'),
                command_type=p.get('commandType'),
                recovery=p.get('recovery')
            )
            for p in records
        ]

    def get_failed_workflow_instances(
        self,
        project_code: int,
        process_definition_code: Optional[int] = None
    ) -> List[WorkflowInstance]:
        """
        获取失败的工作流实例

        Args:
            project_code: 项目编码
            process_definition_code: 工作流定义编码（可选）

        Returns:
            失败的工作流实例列表
        """
        return self.get_workflow_instances(
            project_code=project_code,
            process_definition_code=process_definition_code,
            state_type='FAILURE'
        )

    def get_task_instances(
        self,
        project_code: int,
        process_instance_id: int
    ) -> List[TaskInstance]:
        """
        获取工作流实例的任务列表

        Args:
            project_code: 项目编码
            process_instance_id: 工作流实例 ID

        Returns:
            任务实例列表
        """
        result = self._request(
            'GET',
            f'/projects/{project_code}/process-instances/{process_instance_id}/tasks',
            params={'pageNo': 1, 'pageSize': 1000}
        )

        if not result['success'] or not result['data']:
            self.logger.warning(
                f"获取任务列表失败 - 项目:{project_code}, 实例:{process_instance_id}, "
                f"原因: {result.get('msg', '无数据')}"
            )
            return []

        # DolphinScheduler 3.1.x 返回格式: data.taskList
        # 旧版本返回格式: data.totalList
        records = result['data'].get('taskList', result['data'].get('totalList', []))

        if not records:
            self.logger.debug(
                f"工作流实例 {process_instance_id} 没有任务记录 "
                f"(状态: {result['data'].get('processInstanceState', 'unknown')})"
            )

        return [
            TaskInstance(
                id=t.get('id'),
                name=t.get('name'),
                task_type=t.get('taskType', ''),
                state=t.get('state'),
                max_retry_times=t.get('maxRetryTimes', 0),
                retry_times=t.get('retryTimes', 0),
                process_instance_id=process_instance_id,
                start_time=t.get('startTime'),
                end_time=t.get('endTime')
            )
            for t in records
        ]

    def get_sub_process_instance(
        self,
        project_code: int,
        task_id: int
    ) -> Optional[WorkflowInstance]:
        """
        获取子工作流实例

        Args:
            project_code: 项目编码
            task_id: 任务 ID

        Returns:
            子工作流实例
        """
        result = self._request(
            'GET',
            f'/projects/{project_code}/process-instances/query-sub-by-parent',
            params={'taskId': task_id}
        )

        if not result['success'] or not result['data']:
            return None

        data = result['data']
        return WorkflowInstance(
            id=data.get('id'),
            name=data.get('name'),
            process_definition_code=data.get('processDefinitionCode'),
            project_code=project_code,
            state=data.get('state'),
            run_times=data.get('runTimes', 0),
            start_time=data.get('startTime'),
            end_time=data.get('endTime')
        )

    @monitored(api_name="execute_failure_recovery")
    def execute_failure_recovery(
        self,
        project_code: int,
        process_instance_id: int
    ) -> bool:
        """
        从失败节点恢复执行工作流

        Args:
            project_code: 项目编码
            process_instance_id: 工作流实例 ID

        Returns:
            是否成功
        """
        result = self._request(
            'POST',
            f'/projects/{project_code}/executors/execute',
            data={
                'processInstanceId': process_instance_id,
                'executeType': 'START_FAILURE_TASK_PROCESS'
            }
        )

        return result['success']

    def check_connection(self) -> bool:
        """
        检查 API 连接

        Returns:
            是否连接成功
        """
        try:
            projects = self.get_projects()
            return True
        except Exception as e:
            self.logger.error(f"连接检查失败: {str(e)}")
            return False

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计

        Returns:
            缓存统计信息
        """
        if self._cache:
            return self._cache.get_stats()
        return {'enabled': False}

    def get_metrics_summary(self) -> Dict[str, Any]:
        """
        获取 API 调用统计摘要

        Returns:
            统计摘要
        """
        if self._metrics_collector:
            return self._metrics_collector.get_summary()
        return {'enabled': False}

    def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有 API 的详细指标

        Returns:
            API 指标字典
        """
        if self._metrics_collector:
            return self._metrics_collector.get_all_metrics()
        return {'enabled': False}

    def clear_cache(self) -> None:
        """清空缓存"""
        if self._cache:
            self._cache.clear()
            self.logger.info("API 缓存已清空")

    def reset_metrics(self) -> None:
        """重置监控统计"""
        if self._metrics_collector:
            self._metrics_collector.reset()
            self.logger.info("API 监控统计已重置")

    def print_stats(self) -> None:
        """打印统计信息（用于调试）"""
        if self._cache:
            cache_stats = self.get_cache_stats()
            self.logger.info("=" * 60)
            self.logger.info("API 缓存统计:")
            self.logger.info(f"  缓存大小: {cache_stats['cache_size']}")
            self.logger.info(f"  命中次数: {cache_stats['hit_count']}")
            self.logger.info(f"  未命中次数: {cache_stats['miss_count']}")
            self.logger.info(f"  命中率: {cache_stats['hit_rate']}")

        if self._metrics_collector:
            metrics = self.get_metrics_summary()
            self.logger.info("=" * 60)
            self.logger.info("API 调用统计:")
            self.logger.info(f"  总调用次数: {metrics['total_api_calls']}")
            self.logger.info(f"  总错误次数: {metrics['total_errors']}")
            self.logger.info(f"  错误率: {metrics['error_rate']}")
            self.logger.info(f"  平均耗时: {metrics['avg_duration_ms']} ms")

            if metrics.get('slowest_api'):
                self.logger.info(
                    f"  最慢 API: {metrics['slowest_api']['name']} "
                    f"({metrics['slowest_api']['avg_duration_ms']} ms)"
                )

            if metrics.get('most_called_api'):
                self.logger.info(
                    f"  最频繁 API: {metrics['most_called_api']['name']} "
                    f"({metrics['most_called_api']['call_count']} 次)"
                )
            self.logger.info("=" * 60)
