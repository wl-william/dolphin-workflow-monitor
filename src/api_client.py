"""
DolphinScheduler API 客户端

封装 DolphinScheduler 的 REST API 调用
"""

import requests
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum

from .logger import get_logger


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
        return self.state in FAILURE_STATES

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        return self.state in RUNNING_STATES

    @property
    def is_success(self) -> bool:
        """是否成功"""
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
        return self.state == WorkflowState.FAILURE.value

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        return self.state in {
            WorkflowState.RUNNING_EXECUTION.value,
            WorkflowState.SUBMITTED_SUCCESS.value
        }

    @property
    def is_success(self) -> bool:
        """是否成功"""
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


class DolphinSchedulerClient:
    """DolphinScheduler API 客户端"""

    def __init__(self, api_url: str, token: str):
        """
        初始化客户端

        Args:
            api_url: API 地址
            token: 认证 Token
        """
        self.api_url = api_url.rstrip('/')
        self.token = token
        self.logger = get_logger()
        self.session = requests.Session()
        self.session.headers.update({
            'token': token,
            'Content-Type': 'application/x-www-form-urlencoded'
        })

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

    def get_projects(self) -> List[Project]:
        """
        获取所有项目

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

    def get_process_definitions(
        self,
        project_code: int,
        page_no: int = 1,
        page_size: int = 100
    ) -> List[ProcessDefinition]:
        """
        获取项目下的工作流定义

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
            return []

        records = result['data'].get('totalList', [])
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
