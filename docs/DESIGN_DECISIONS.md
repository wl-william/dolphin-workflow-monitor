# 设计决策记录

本文档记录项目中重要的设计决策及其理由，便于后续维护时理解上下文。

---

## 1. 工作流状态监控范围

### 决策

**只监控 `FAILURE` 状态的工作流实例，不监控 `STOP`、`KILL` 状态。**

### 涉及代码

- `src/api_client.py` — `get_failed_workflow_instances()` 只查询 `stateType='FAILURE'`
- `src/api_client.py` — `WorkflowInstance.is_failed` 只判断 `FAILURE` 状态

### DolphinScheduler 工作流状态一览

| 状态 | 编码 | 是否监控 | 说明 |
|------|------|----------|------|
| `FAILURE` | 6 | **是** | 工作流执行失败，属于异常情况，需要自动恢复 |
| `STOP` | 5 | **否** | 人为主动停止，属于正常操作 |
| `KILL` | 9 | **否** | 人为主动终止，属于正常操作 |
| `SUCCESS` | 7 | 否 | 执行成功 |
| `RUNNING_EXECUTION` | 1 | 否 | 执行中 |
| `PAUSE` | 3 | 否 | 已暂停 |
| `NEED_FAULT_TOLERANCE` | 8 | 否 | 容错处理中，DolphinScheduler 自行处理 |

### 理由

- `STOP` 和 `KILL` 是运维人员主动操作的结果，不属于需要自动恢复的异常
- 对这些状态执行自动恢复会与人为操作意图冲突
- `NEED_FAULT_TOLERANCE` 由 DolphinScheduler 内部容错机制处理

### 变更注意

如果未来需要扩展监控范围（如监控 `STOP` 状态），需要同时修改：
1. `api_client.py` 的 `get_failed_workflow_instances()` 方法
2. `api_client.py` 的 `WorkflowInstance.is_failed` 属性
3. `task_validator.py` 的验证逻辑

---

## 2. 调度优化中 SUCCESS 状态的处理

### 决策

**本周期已成功的工作流不跳过 API 查询，仍需持续监控。**

### 涉及代码

- `src/schedule_tracker.py` — `make_decision()` 方法

### 理由

之前的实现中，一旦工作流在某个调度周期内被标记为 `SUCCESS`，后续检查将完全跳过该工作流的 API 查询。
这会导致以下场景中 FAILURE 实例被漏检：

1. 工作流首次执行成功 -> 标记 SUCCESS -> 跳过后续检查
2. 工作流被手动重新触发或重新调度 -> 执行失败
3. 失败实例永远不会被检测到，直到下一个调度周期重置

修复后，SUCCESS 状态仍会执行 API 查询，确保新产生的 FAILURE 实例能被及时发现。
由于 API 查询采用批量模式（一次查询项目下所有失败实例），额外开销可忽略。
