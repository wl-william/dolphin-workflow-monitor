# DolphinScheduler 工作流监控器

自动化运维工具，用于监控和恢复 DolphinScheduler 工作流。

## 功能特性

- ✅ 获取 DolphinScheduler 项目中工作流执行状态
- ✅ 可配置需要检测的项目及工作流
- ✅ 自动过滤执行失败的任务
- ✅ **智能任务验证**：重试前验证所有任务状态
  - 确保工作流中所有任务都已失败或完成
  - 验证每个任务配置的重试次数已全部用完
  - 检查是否有任务仍在运行中
  - 支持嵌套工作流（子工作流）的递归验证
- ✅ 智能重试机制（支持最大重试次数限制）
- ✅ 持续监控模式
- ✅ 支持多项目监控
- ✅ 灵活的配置管理（环境变量、配置文件）
- ✅ 详细的日志记录

## 快速开始

### 方式一：Docker 部署（推荐）

#### ⚠️ 重要：解决日志文件权限问题

如果遇到权限错误 `Permission denied: '/app/logs/monitor.log'`，请查看详细解决方案：

📖 **[Docker 部署权限问题完整解决方案](DOCKER_DEPLOYMENT.md)**

**快速修复（3选1）**：

```bash
# 方案1: 预设目录权限（最快）
sudo bash scripts/setup-logs.sh
docker-compose up -d

# 方案2: 自定义用户ID（推荐生产环境）
echo "USER_ID=$(id -u)" >> .env
echo "GROUP_ID=$(id -g)" >> .env
docker-compose build
docker-compose up -d

# 方案3: 使用命名卷（最简单）
docker-compose -f docker-compose.named-volume.yaml up -d
```

#### 标准部署步骤

1. **克隆项目**
```bash
git clone <repository-url>
cd dolphin-workflow-monitor
```

2. **配置环境变量**
```bash
cp .env.example .env
# 编辑 .env 文件，配置必要参数
# 重要：添加 USER_ID 和 GROUP_ID 避免权限问题
echo "USER_ID=$(id -u)" >> .env
echo "GROUP_ID=$(id -g)" >> .env
```

3. **启动服务**
```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 方式二：本地运行

1. **安装依赖**
```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. **配置环境变量**
```bash
cp .env.example .env
# 编辑 .env 文件
```

3. **运行监控**
```bash
python main.py run
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DS_API_URL` | DolphinScheduler API 地址 | `http://localhost:12345/dolphinscheduler` |
| `DS_TOKEN` | 认证 Token（必填） | - |
| `DS_CHECK_INTERVAL` | 检查间隔（秒） | `60` |
| `DS_CONTINUOUS_MODE` | 持续监控模式 | `true` |
| `DS_AUTO_RECOVERY` | 自动恢复开关 | `true` |
| `DS_MAX_RECOVERY_ATTEMPTS` | 最大恢复次数 | `3` |
| `DS_LOG_LEVEL` | 日志级别 | `INFO` |
| `USER_ID` | Docker 容器用户 ID（解决权限问题） | `1000` |
| `GROUP_ID` | Docker 容器用户组 ID（解决权限问题） | `1000` |

### 配置文件

编辑 `config/config.yaml` 配置需要监控的项目和工作流：

```yaml
# 需要监控的项目和工作流配置
projects:
  # 监控所有工作流
  my_project_1:
    workflows: []
    monitor_all: true

  # 只监控指定工作流
  my_project_2:
    workflows:
      - workflow_name_1
      - workflow_name_2
    monitor_all: false
```

## 命令行使用

### 启动持续监控

```bash
python main.py run
# 或使用配置文件
python main.py run -c /path/to/config.yaml
```

### 执行单次检查

```bash
python main.py check
```

### 测试 API 连接

```bash
python main.py test-connection
```

### 列出工作流状态

```bash
# 列出项目中所有工作流
python main.py list-workflows -p my_project

# 查看指定工作流
python main.py list-workflows -p my_project -w my_workflow
```

### 验证工作流实例

检查指定工作流实例是否满足恢复条件：

```bash
python main.py validate-workflow -p my_project -i 12345
```

### 手动恢复工作流

```bash
# 正常恢复（会进行验证）
python main.py recover -p my_project -i 12345

# 强制恢复（跳过验证）
python main.py recover -p my_project -i 12345 --force
```

### 查看统计信息

```bash
python main.py stats
```

### 清除恢复记录

```bash
# 清除指定工作流的恢复记录
python main.py clear-records -i 12345

# 清除所有恢复记录
python main.py clear-records
```

### 显示当前配置

```bash
python main.py show-config
```

## 恢复逻辑说明

### 恢复条件

工作流实例必须同时满足以下条件才会执行恢复：

1. **工作流状态为失败**：工作流整体状态必须是 `FAILURE`
2. **没有运行中的任务**：所有任务都已完成执行
3. **存在失败的任务**：至少有一个任务处于失败状态
4. **重试次数已用完**：所有失败任务的重试次数都已达到配置的最大值
5. **未超过恢复次数限制**：该工作流实例的恢复次数未超过 `max_recovery_attempts`

### 嵌套工作流处理

对于包含子工作流（SUB_PROCESS 类型任务）的工作流，监控器会：

1. 识别 SUB_PROCESS 类型的任务
2. 获取子工作流实例信息
3. 递归验证子工作流中的所有任务
4. 只有当主工作流和所有子工作流都满足恢复条件时，才执行恢复

### 任务状态说明

| 状态 | 说明 | 分类 |
|------|------|------|
| `SUCCESS` | 成功 | 完成 |
| `FAILURE` | 失败 | 失败 |
| `KILL` | 被终止 | 失败 |
| `RUNNING_EXECUTION` | 执行中 | 运行中 |
| `SUBMITTED_SUCCESS` | 提交成功 | 运行中 |
| `WAITING_DEPEND` | 等待依赖 | 运行中 |

## 日志

日志文件位于 `logs/monitor.log`，包含：

- 监控检查记录
- 失败工作流发现
- 验证过程详情
- 恢复操作结果
- 错误信息

## 项目结构

```
dolphin-workflow-monitor/
├── config/
│   └── config.yaml           # 配置文件
├── logs/                      # 日志目录
├── scripts/
│   ├── start.sh              # 本地启动脚本
│   ├── docker-start.sh       # Docker 启动脚本
│   └── setup-logs.sh         # 日志目录权限设置脚本
├── src/
│   ├── __init__.py
│   ├── api_client.py         # DolphinScheduler API 客户端
│   ├── cli.py                # 命令行接口
│   ├── config.py             # 配置管理
│   ├── logger.py             # 日志模块
│   ├── monitor.py            # 工作流监控器
│   ├── recovery_handler.py   # 恢复处理器
│   └── task_validator.py     # 任务验证器
├── .env.example              # 环境变量示例
├── .gitignore
├── docker-compose.yaml       # Docker Compose 配置
├── docker-compose.named-volume.yaml  # 使用命名卷的配置
├── Dockerfile
├── DOCKER_DEPLOYMENT.md      # Docker 部署权限问题解决方案
├── main.py                   # 主入口
├── README.md
├── requirements.txt
└── setup.py
```

## 获取 DolphinScheduler Token

1. 登录 DolphinScheduler Web UI
2. 点击右上角用户名 -> 安全中心 -> Token 管理
3. 创建 Token 并复制

或通过 API 获取：

```bash
curl -X POST "http://your-ds-server:12345/dolphinscheduler/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "userName=admin&userPassword=your_password"
```

## 常见问题

### Q: Docker 部署遇到权限错误怎么办？

**错误**：`PermissionError: [Errno 13] Permission denied: '/app/logs/monitor.log'`

**解决**：查看详细文档 [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)

**快速修复**：
```bash
# 运行权限设置脚本
sudo bash scripts/setup-logs.sh
```

### Q: 如何调整检查频率？

修改环境变量 `DS_CHECK_INTERVAL` 或配置文件中的 `monitor.check_interval`。

### Q: 如何禁用自动恢复？

设置环境变量 `DS_AUTO_RECOVERY=false`，监控器将只记录日志而不执行恢复操作。

### Q: 如何监控多个项目？

在 `config/config.yaml` 中添加多个项目配置即可。

### Q: 恢复失败怎么办？

检查日志了解失败原因。恢复记录会保存在 `logs/recovery_state.json` 中，可以使用 `clear-records` 命令重置。

### Q: 如何在需要 sudo 权限的机器上部署？

参考 [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) 中的方案 2（自定义用户 UID/GID）或方案 3（使用命名卷），这两种方案都不需要 sudo 权限。

## 文档

- [README.md](README.md) - 项目总览和使用指南（本文档）
- [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) - Docker 部署权限问题完整解决方案

## 许可证

MIT License
