# DolphinScheduler 工作流监控器 — 详细设计文档

## 1. 系统概述

### 1.1 定位

自动化监控 DolphinScheduler 平台上的工作流执行状态，发现失败后自动恢复，并通过多渠道通知相关人员。

### 1.2 核心能力

- 周期性检查工作流实例状态（FAILURE）
- 失败工作流自动恢复（从失败节点重跑）
- 多通知渠道（钉钉、企业微信、邮件）
- 智能调度优化（减少无效 API 调用）
- 通知限流（防止告警风暴）

### 1.3 技术栈

| 项目     | 选型                  |
| -------- | --------------------- |
| 语言     | Python 3.11           |
| CLI 框架 | Click                 |
| HTTP     | Requests + HTTPAdapter |
| 部署     | Docker / Docker Compose |
| 配置     | YAML + 环境变量       |
| 日志     | logging + RotatingFileHandler |

---

## 2. 架构图

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         main.py (入口)                              │
│                             │                                       │
│                         CLI (click)                                 │
│              ┌──────────────┼──────────────┐                        │
│              │              │              │                        │
│            run           check       test_connection ...            │
│              │              │                                       │
│              └──────┬───────┘                                       │
│                     ▼                                               │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   WorkflowMonitor (核心调度器)                │   │
│  │                                                              │   │
│  │  check_once()  ──── 每个检查周期的入口                       │   │
│  │       │                                                      │   │
│  │       ▼                                                      │   │
│  │  _check_project()  ──── 逐项目检查                           │   │
│  │       │                                                      │   │
│  │       ├──▶ ScheduleTracker ──── 决策：是否需要查 API          │   │
│  │       │        │                                             │   │
│  │       │        └── CronParser ──── 解析 cron，检测周期切换    │   │
│  │       │                                                      │   │
│  │       ├──▶ DolphinSchedulerClient ──── 调用 DS API           │   │
│  │       │        │                                             │   │
│  │       │        ├── APICache ──── 缓存（TTL 1h）              │   │
│  │       │        └── APIMetrics ──── 调用计数 & 耗时统计        │   │
│  │       │                                                      │   │
│  │       ├──▶ RecoveryHandler ──── 执行恢复                     │   │
│  │       │        │                                             │   │
│  │       │        └── TaskValidator ──── 验证工作流是否可恢复    │   │
│  │       │                                                      │   │
│  │       └──▶ NotificationManager ──── 发送通知                 │   │
│  │                │                                             │   │
│  │                ├── DingTalkNotifier                           │   │
│  │                ├── WeWorkNotifier                             │   │
│  │                └── EmailNotifier                              │   │
│  │                                                              │   │
│  │            NotificationRateLimiter ──── 通知限流              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌────────────────────────────────────────┐                         │
│  │           Config (配置管理)             │                         │
│  │  YAML 文件 + 环境变量 → 统一配置对象   │                         │
│  └────────────────────────────────────────┘                         │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP/HTTPS (Token 认证)
                              ▼
                 ┌─────────────────────────┐
                 │  DolphinScheduler API   │
                 │  /projects/...          │
                 │  /process-instances/... │
                 │  /executors/execute     │
                 └─────────────────────────┘
```

### 2.2 数据流

```
                    ┌──────────────┐
                    │ DS API Server│
                    └──────┬───────┘
                           │
            ┌──────────────┼──────────────────────┐
            │              │                      │
            ▼              ▼                      ▼
     get_projects()  get_failed_       get_success_
     get_process_    workflow_         workflow_
     definitions()   instances()       instances()
            │              │                      │
            │         ┌────┴────┐                 │
            │         │ 有失败？ │                 │
            │         └────┬────┘                 │
            │          Y   │   N                  │
            │         ┌────┘   └──────────────────┤
            │         ▼                           ▼
            │   mark_failed()            mark_success()
            │   + 阈值判断                (仅当确认有 SUCCESS 实例)
            │         │
            │    ┌────┴────┐
            │    │超过阈值？│
            │    └────┬────┘
            │     Y   │   N
            │    ┌────┘   └────┐
            │    ▼              ▼
            │ 只通知       执行恢复
            │ (限流)    execute_failure_
            │            recovery()
            │              │
            │         ┌────┴────┐
            │         │恢复成功？│
            │         └────┬────┘
            │          Y   │   N
            │         ┌────┘   └────┐
            │         ▼              ▼
            │  mark_recovered()   保持 FAILED
            │  + 通知成功         + 通知失败
            │
            ▼
     注册到 ScheduleTracker
     (cron 表达式 → 周期检测)
```

### 2.3 状态机

```
ScheduleTracker 中每个工作流在一个调度周期内的状态流转：

                    ┌─────────────────────────────┐
                    │      新周期开始（cron 触发）   │
                    │      重置所有状态             │
                    └──────────┬──────────────────┘
                               ▼
                         ┌──────────┐
                    ┌───▶│ PENDING  │◀─────────────────────────┐
                    │    └────┬─────┘                           │
                    │         │ API 查询                        │
                    │         │                                 │
                    │    ┌────┴──────────────┐                  │
                    │    │                   │                  │
                    │    ▼                   ▼                  │
                    │ 查到 FAILURE      查到 SUCCESS 实例       │
                    │    │                   │                  │
                    │    ▼                   ▼                  │
                    │ ┌──────────┐    ┌──────────┐             │
                    │ │  FAILED  │    │ SUCCESS  │             │
                    │ └────┬─────┘    └──────────┘             │
                    │      │           （跳过后续 API 查询，     │
                    │      │            直到下个周期）           │
                    │      │                                    │
                    │      │ 执行恢复                           │
                    │      ▼                                    │
                    │ ┌────────────┐                            │
                    │ │ RECOVERED  │───── 继续监控 ─────────────┘
                    │ └────────────┘      (可能再次失败)
                    │
                    └── 下个周期开始时重置
```

---

## 3. 模块详细设计

### 3.1 配置管理 (`src/config.py`)

#### 职责

解析 YAML 配置文件和环境变量，生成统一的强类型配置对象。

#### 配置优先级

```
环境变量 > YAML 文件 > dataclass 默认值
```

#### 数据结构

```python
@dataclass
class ProjectConfig:
    name: str                      # 项目名称（对应 DS UI 上的名称）
    workflows: List[str]           # 需监控的工作流名称列表
    monitor_all: bool = True       # True: 监控项目下所有工作流

@dataclass
class MonitorConfig:
    check_interval: int = 60                    # 检查间隔（秒）
    continuous_mode: bool = True                 # 持续模式 / 单次模式
    timeout: int = 300                           # 单次模式超时（秒）
    time_window_hours: int = 24                  # 只关注 N 小时内启动的工作流
    max_failures_for_recovery: int = 1           # 阈值：超过则只通知不恢复
    enable_schedule_optimization: bool = True    # 启用调度感知优化

@dataclass
class RetryConfig:
    max_recovery_attempts: int = 3    # 单实例最大恢复次数
    recovery_interval: int = 30       # 恢复操作间隔（秒）
    auto_recovery: bool = True        # 是否自动恢复

@dataclass
class DolphinConfig:
    api_url: str     # DS API 地址
    token: str       # 认证 Token

@dataclass
class NotificationConfig:
    dingtalk: DingTalkConfig
    wework: WeWorkConfig
    email: EmailConfig
```

#### 环境变量映射

| 环境变量                         | 对应配置                                   |
| -------------------------------- | ------------------------------------------ |
| `DS_API_URL`                     | `dolphinscheduler.api_url`                 |
| `DS_TOKEN`                       | `dolphinscheduler.token`                   |
| `DS_CHECK_INTERVAL`              | `monitor.check_interval`                   |
| `DS_CONTINUOUS_MODE`             | `monitor.continuous_mode`                  |
| `DS_TIME_WINDOW_HOURS`           | `monitor.time_window_hours`                |
| `DS_MAX_FAILURES_FOR_RECOVERY`   | `monitor.max_failures_for_recovery`        |
| `DS_ENABLE_SCHEDULE_OPTIMIZATION`| `monitor.enable_schedule_optimization`     |
| `DS_MAX_RECOVERY_ATTEMPTS`       | `retry.max_recovery_attempts`              |

---

### 3.2 API 客户端 (`src/api_client.py`)

#### 职责

封装 DolphinScheduler REST API，提供类型安全的 Python 接口。

#### 核心类型

```python
class WorkflowState(Enum):
    SUBMITTED_SUCCESS = 0
    RUNNING_EXECUTION = 1
    READY_PAUSE = 2
    PAUSE = 3
    READY_STOP = 4
    STOP = 5
    FAILURE = 6       # ← 监控器关注的失败状态
    SUCCESS = 7       # ← 用于确认成功
    NEED_FAULT_TOLERANCE = 8
    KILL = 9
    ...

@dataclass
class WorkflowInstance:
    id: int
    name: str
    process_definition_code: int
    project_code: int
    state: int                       # WorkflowState 值
    run_times: int
    start_time: Optional[str]
    end_time: Optional[str]
    command_type: Optional[str]
    recovery: Optional[str]

    is_failed → bool     # state == FAILURE
    is_running → bool    # state in RUNNING_STATES
    is_success → bool    # state in SUCCESS_STATES

@dataclass
class Project:
    id: int
    code: int
    name: str

@dataclass
class ProcessDefinition:
    id: int
    code: int
    name: str
    project_code: int

@dataclass
class WorkflowSchedule:
    crontab: str            # Cron 表达式
    start_time: str
    end_time: str
    timezone_id: str
    release_state: str      # ONLINE / OFFLINE
    process_definition_code: int
```

#### API 方法清单

| 方法                             | DS API 端点                                         | 用途                   |
| -------------------------------- | --------------------------------------------------- | ---------------------- |
| `get_projects()`                 | `GET /projects`                                     | 列出所有项目           |
| `get_process_definitions()`      | `GET /projects/{code}/process-definition`            | 获取工作流定义         |
| `get_workflow_schedules()`       | `GET /projects/{code}/schedule/list`                 | 获取调度信息           |
| `get_workflow_instances()`       | `GET /projects/{code}/process-instances`              | 查询工作流实例         |
| `get_failed_workflow_instances()`| 同上，`stateType=FAILURE`                            | 查询失败实例           |
| `get_success_workflow_instances()`| 同上，`stateType=SUCCESS`                           | 查询成功实例           |
| `get_task_instances()`           | `GET /projects/{code}/task-instances`                 | 查询任务实例           |
| `execute_failure_recovery()`     | `POST /projects/{code}/executors/execute`             | 从失败节点恢复         |
| `check_connection()`             | `GET /projects`                                      | 连通性检查             |

#### 弹性设计

| 机制         | 参数                                      |
| ------------ | ----------------------------------------- |
| 重试策略     | 3 次，指数退避 0.5s → 1s → 2s             |
| 重试状态码   | 429, 500, 502, 503, 504                   |
| 连接池       | 10 连接，最大 20                          |
| 超时         | 30 秒                                    |
| 缓存         | TTL 1 小时（项目/工作流定义/调度信息）     |

---

### 3.3 API 缓存 (`src/api_cache.py`)

#### 职责

对不频繁变化的 API 响应（项目列表、工作流定义、调度信息）进行缓存，减少 API 调用。

#### 设计

```python
@dataclass
class CacheEntry[T]:
    value: T
    expire_at: float          # Unix 时间戳

class APICache:
    _cache: Dict[str, CacheEntry]
    _lock: Lock               # 线程安全

    get(key) → Optional[T]    # 命中/未命中计数
    set(key, value, ttl)      # 默认 TTL 3600 秒
    clean_expired()           # 清理过期条目
    get_stats() → dict        # cache_size, hit_count, miss_count, hit_rate
```

#### `@cached` 装饰器

```python
@cached(ttl_seconds=3600, key_prefix="projects")
def get_projects(self):
    ...
```

自动生成缓存键：`{key_prefix}_{函数名}_{参数哈希}`，命中则直接返回，未命中则调用原函数并缓存结果。

---

### 3.4 API 指标 (`src/api_metrics.py`)

#### 职责

采集每个 API 方法的调用次数、错误次数、耗时统计。

#### 设计

```python
@dataclass
class APIMetric:
    call_count: int
    error_count: int
    total_duration: float
    min_duration: float
    max_duration: float
    avg_duration → float      # 计算属性（毫秒）

class APIMetricsCollector:
    _metrics: Dict[str, APIMetric]
    _lock: Lock

    record_call(api_name, duration, is_error)
    get_summary() → {
        total_api_calls,
        total_errors,
        error_rate,
        avg_duration_ms,
        slowest_api,
        most_called_api
    }
```

#### `@monitored` 装饰器

```python
@monitored(api_name="get_workflow_instances")
def get_workflow_instances(self, ...):
    ...
```

自动记录调用耗时和是否报错。

---

### 3.5 Cron 解析器 (`src/cron_parser.py`)

#### 职责

解析 DolphinScheduler 的 cron 表达式（6-7 字段格式），计算上次/下次调度时间，用于调度周期检测。

#### Cron 格式

```
秒 分 时 日 月 周 [年]
```

支持语法：`*`、`n`、`n-m`（范围）、`*/n`（步长）、`n,m`（枚举）。

#### 核心方法

```python
class CronParser:
    def get_schedule_times(reference_time) → (last_schedule, next_schedule)
    def get_schedule_period(reference_time) → SchedulePeriod

@dataclass
class SchedulePeriod:
    current_start: datetime    # 上次调度时间（= 当前周期开始）
    current_end: datetime      # 下次调度时间（= 当前周期结束）
    next_start: datetime       # 下次调度时间
    is_in_execution_window: bool
```

#### 非日调度的降级策略

对于周调度（`0 0 2 * * MON`）、月调度（`0 0 15 * *`）等复杂表达式，无法精确计算跨日的上次/下次调度时间，降级为**以每天的调度时刻为周期边界**，确保每天至少重置一次状态。

---

### 3.6 调度追踪器 (`src/schedule_tracker.py`)

#### 职责

追踪每个工作流在当前调度周期内的状态，决定是否需要调用 API 检查。

#### 周期定义

**周期 = `[上次调度时间, 下次调度时间)`**

由 `CronParser` 根据 cron 表达式和当前时间计算。当 `current_start` 发生变化时，进入新周期，状态自动重置为 `PENDING`。

示例（cron: 每天 02:00）：
- 3月19日 10:00 → 周期 `[3月19日 02:00, 3月20日 02:00)`
- 3月20日 03:00 → 周期 `[3月20日 02:00, 3月21日 02:00)`，状态重置为 PENDING

#### 状态枚举

```python
class WorkflowPeriodStatus(Enum):
    PENDING   = "pending"      # 待确认（尚未通过 API 确认结果）
    SUCCESS   = "success"      # 本周期已确认成功
    FAILED    = "failed"       # 本周期失败，需要持续监控
    RECOVERED = "recovered"    # 已提交恢复
```

#### 决策逻辑

```python
def make_decision(project_code, workflow_code) → MonitorDecision:
    update_period()          # 检测新周期，若新周期则重置为 PENDING
    if status == SUCCESS:
        return 跳过           # 本周期已确认成功，无需再查
    else:
        return 检查           # PENDING / FAILED / RECOVERED 都需要查 API
```

**为什么只有 SUCCESS 才跳过**：
- `PENDING`：未确认结果，必须查询
- `FAILED`：失败中，需持续监控是否被恢复
- `RECOVERED`：恢复后可能再次失败，需持续监控

#### 持久化

状态保存在 `data/schedule_state.json`，确保程序重启后不丢失状态。

---

### 3.7 监控核心 (`src/monitor.py`)

#### 职责

顶层调度器，串联所有组件，执行完整的监控-恢复-通知流程。

#### 核心数据结构

```python
@dataclass
class MonitoredProject:
    config: ProjectConfig               # 项目配置
    project_code: Optional[int]         # 解析后的项目编码
    workflow_codes: Dict[str, int]      # {工作流名称: 工作流编码}
    status: str                         # pending / active / not_found

@dataclass
class MonitorStats:
    check_count: int                    # 总检查次数
    failed_workflows_found: int         # 发现的失败工作流数
    recovery_attempts: int              # 恢复尝试次数
    successful_recoveries: int          # 恢复成功次数
    skipped_due_to_threshold: int       # 因阈值跳过的数量
    skipped_due_to_schedule: int        # 因调度优化跳过的数量
    api_calls_saved: int                # 节省的 API 调用次数
    workflow_failure_stats: Dict        # {项目: {工作流: 失败数}}
```

#### `_check_project()` 完整流程

```
输入: MonitoredProject
输出: List[RecoveryResult]

1. 确定需检查的工作流列表
   ├─ monitor_all=true → 直接查项目全部失败实例
   └─ monitor_all=false → ScheduleTracker 过滤 → 批量查项目失败实例 → 本地过滤

2. 确认成功状态（mark_success 优化）
   ├─ 查 SUCCESS 实例（额外 1 次 API 调用）
   └─ 对有 SUCCESS 实例且无 FAILURE 的工作流 → mark_success
      （注意：不是"没查到 FAILURE 就标记成功"，防止 RUNNING 状态被误标）

3. 时间窗口过滤
   └─ 只保留 time_window_hours（默认 24 小时）内启动的失败实例

4. 按工作流定义分组统计
   └─ {process_definition_code: [WorkflowInstance, ...]}

5. 阈值判断（per 工作流定义）
   ├─ 失败数 > max_failures_for_recovery → 只通知不恢复（限流）
   └─ 失败数 ≤ 阈值 → 执行恢复

6. 恢复 & 通知
   ├─ recovery_handler.process_failed_workflow()
   ├─ 成功 → mark_recovered + 通知
   └─ 失败 → 保持 FAILED + 通知
```

#### 信号处理

```python
signal.signal(signal.SIGINT, handler)    # Ctrl+C
signal.signal(signal.SIGTERM, handler)   # docker stop
```

收到信号后设置 `_stop_event`，监控循环在下次 `wait()` 时退出，打印摘要统计。

---

### 3.8 任务验证器 (`src/task_validator.py`)

#### 职责

验证工作流实例是否满足恢复条件。

#### 当前逻辑（简化版）

```python
def validate_workflow_instance(project_code, workflow_instance):
    if workflow_instance.is_failed:
        return READY_FOR_RECOVERY
    else:
        return NO_FAILED_TASKS
```

只检查工作流整体状态是否为 FAILURE，不深入检查内部任务。具体哪些任务需要重跑由 DolphinScheduler 自身决定。

---

### 3.9 恢复处理器 (`src/recovery_handler.py`)

#### 职责

管理恢复操作的执行、计数、限制和持久化。

#### 恢复流程

```
process_failed_workflow(project_code, workflow_instance)
    │
    ├─ 1. validate_workflow_instance() → 是否 FAILURE？
    │     └─ 否 → 返回（不恢复）
    │
    ├─ 2. 检查恢复次数 ≥ max_recovery_attempts？
    │     └─ 是 → 返回（已达上限）
    │
    ├─ 3. 检查 auto_recovery 是否启用？
    │     └─ 否 → 返回（自动恢复已禁用）
    │
    ├─ 4. execute_failure_recovery()
    │     └─ POST /executors/execute
    │        executeType = START_FAILURE_TASK_PROCESS
    │
    └─ 5. 记录结果 → 持久化到 recovery_state.json
```

#### 持久化结构

```json
// logs/recovery_state.json
{
  "361685": {
    "workflow_instance_id": 361685,
    "workflow_name": "新加坡报表数据回国-日报表",
    "project_code": 10147222749280,
    "attempt_count": 4,
    "last_attempt_time": "2026-03-19T02:30:00",
    "recovery_history": [
      {"attempt": 1, "time": "...", "success": true, "message": "..."},
      {"attempt": 2, "time": "...", "success": true, "message": "..."}
    ]
  }
}
```

---

### 3.10 通知系统 (`src/notifiers/`)

#### 架构

```
NotificationManager (分发器)
    │
    ├── DingTalkNotifier    ── Webhook + HMAC 签名 + Markdown
    ├── WeWorkNotifier      ── Webhook + Markdown
    └── EmailNotifier       ── SMTP + HTML

NotificationRateLimiter (限流器)
    └── 每个工作流每 24 小时最多 6 条通知
```

#### 消息类型

| 构建函数                              | 触发场景                       | 级别    |
| ------------------------------------- | ------------------------------ | ------- |
| `build_failure_detected_message()`    | 检测到工作流失败               | ERROR   |
| `build_recovery_success_message()`    | 恢复操作成功提交               | SUCCESS |
| `build_recovery_failed_message()`     | 恢复操作执行失败               | ERROR   |
| `build_threshold_exceeded_message()`  | 同一工作流短时间内多次失败     | WARNING |

#### 通知限流

```python
class NotificationRateLimiter:
    time_window_hours: int = 24    # 时间窗口
    max_notifications: int = 6     # 窗口内最大通知数

    # 键格式: "{project_name}:{workflow_definition_code}"
    # 持久化: logs/notification_rate_limit.json
```

超出限额时在日志中记录跳过原因，不发送通知。

---

### 3.11 CLI 命令 (`src/cli.py`)

| 命令               | 用途               | 关键参数                        |
| ------------------ | ------------------ | ------------------------------- |
| `run`              | 持续监控           | `-c` 配置文件路径               |
| `check`            | 单次检查           | `-c` 配置文件路径               |
| `test_connection`  | 测试 API 连通性    | `-c` 配置文件路径               |
| `list_workflows`   | 列出工作流实例     | `-p` 项目名, `-w` 工作流名     |
| `validate_workflow`| 验证恢复条件       | `-p` 项目名, `-i` 实例 ID      |
| `recover`          | 手动恢复           | `-p` 项目名, `-i` 实例 ID, `-f` 强制 |
| `stats`            | 查看恢复统计       | 无                              |
| `clear_records`    | 清除恢复记录       | `-i` 实例 ID（可选）            |
| `show_config`      | 显示当前配置       | `-c` 配置文件路径               |

---

## 4. 线程安全模型

| 组件                    | 锁类型   | 保护对象                        |
| ----------------------- | -------- | ------------------------------- |
| `ScheduleTracker`       | `RLock`  | `_states` 字典                  |
| `APICache`              | `Lock`   | `_cache` 字典                   |
| `APIMetricsCollector`   | `Lock`   | `_metrics` 字典                 |
| `NotificationRateLimiter`| `Lock`  | `_records` 字典                 |
| `DolphinSchedulerClient`| 无需     | `requests.Session` 自身线程安全 |

程序主体为单线程循环，锁主要保护信号处理线程并发访问和未来多线程扩展。

---

## 5. 持久化文件清单

| 文件路径                            | 用途                 | 格式       | 写入时机                    |
| ----------------------------------- | -------------------- | ---------- | --------------------------- |
| `logs/monitor.log`                  | 运行日志             | 文本       | 实时写入（RotatingFileHandler） |
| `logs/recovery_state.json`          | 恢复记录             | JSON       | 每次恢复尝试后              |
| `data/schedule_state.json`          | 调度周期状态         | JSON       | 状态变更时                  |
| `logs/notification_rate_limit.json` | 通知限流记录         | JSON       | 每次发送通知后              |

---

## 6. 关键设计决策

### 6.1 "确认成功"而非"没有失败"

`mark_success` 只在 API 返回了 SUCCESS 状态的工作流实例时才标记，不因为"没查到 FAILURE"就标记成功。原因：工作流可能还在 RUNNING，此时查不到 FAILURE 也查不到 SUCCESS，不应跳过后续检查。

### 6.2 批量查询 + 本地过滤

对于 `monitor_all=false` 的项目，不逐个查询每个工作流的失败实例，而是一次查询项目所有失败实例，在本地按 `process_definition_code` 过滤。将 N 次 API 调用降为 1 次。

### 6.3 阈值判断按工作流定义独立计算

同一个工作流定义在 24 小时内有多个实例失败（如 3 个），超过阈值（默认 1）则只通知不恢复，防止对系统性故障反复执行无效恢复。不同工作流定义之间互不影响。

### 6.4 通知限流独立于恢复逻辑

通知限流器（每工作流每 24 小时最多 6 条）仅控制通知频率，不影响恢复操作的执行判断。

---

## 7. 部署架构

### Docker 部署

```
┌─────────────────────────────────┐
│        Docker Container         │
│                                 │
│  python:3.11-slim               │
│  User: monitor (UID 1000)       │
│                                 │
│  /app/                          │
│    ├── main.py                  │
│    ├── src/                     │
│    └── config/ (只读挂载)       │
│                                 │
│  /app/logs/  (持久化卷)         │
│  /app/data/  (持久化卷)         │
└────────────┬────────────────────┘
             │
             │ HTTP (Token 认证)
             ▼
     DolphinScheduler API
```

### Docker Compose 配置要点

```yaml
services:
  dolphin-monitor:
    volumes:
      - ./config:/app/config:ro        # 配置只读
      - ./logs:/app/logs               # 日志持久化
    environment:
      - DS_TOKEN=${DS_TOKEN}           # Token 通过环境变量注入
      - DS_API_URL=http://ds-host:port/dolphinscheduler
    dns:
      - 223.5.5.5                      # 阿里云 DNS（容器内解析）
      - 8.8.8.8
```

---

## 8. 文件结构

```
dolphin-workflow-monitor/
├── main.py                              # 入口
├── requirements.txt                     # 依赖
├── setup.py                             # 包配置
├── Dockerfile                           # 容器镜像
├── docker-compose.yaml                  # 服务编排
├── DESIGN.md                            # 本文档
│
├── config/
│   └── config.yaml                      # 配置文件（含详细注释）
│
├── src/
│   ├── __init__.py
│   ├── cli.py                           # CLI 命令定义
│   ├── config.py                        # 配置解析
│   ├── logger.py                        # 日志管理（单例 + 彩色输出）
│   ├── api_client.py                    # DS API 客户端（HTTP + 重试 + 连接池）
│   ├── api_cache.py                     # API 响应缓存（TTL + 线程安全）
│   ├── api_metrics.py                   # API 调用指标采集
│   ├── cron_parser.py                   # Cron 表达式解析器
│   ├── schedule_tracker.py              # 调度周期状态追踪器
│   ├── task_validator.py                # 工作流恢复条件验证
│   ├── recovery_handler.py              # 恢复操作执行 & 计数
│   ├── monitor.py                       # 监控核心调度器
│   │
│   └── notifiers/
│       ├── __init__.py
│       ├── base.py                      # 通知抽象基类 + NotificationManager
│       ├── factory.py                   # 通知器工厂
│       ├── message_builder.py           # 消息模板构建
│       ├── rate_limiter.py              # 通知限流器
│       ├── dingtalk.py                  # 钉钉通知（Webhook + HMAC 签名）
│       ├── wework.py                    # 企业微信通知（Webhook）
│       └── email.py                     # 邮件通知（SMTP + HTML）
│
├── logs/                                # 运行时生成
│   ├── monitor.log                      # 主日志（滚动）
│   ├── recovery_state.json              # 恢复记录
│   └── notification_rate_limit.json     # 通知限流记录
│
└── data/                                # 运行时生成
    └── schedule_state.json              # 调度周期状态
```
