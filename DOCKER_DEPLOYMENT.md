# Docker 部署指南

本文档提供解决 Docker 部署中权限问题的完整方案。

## ❌ 常见错误

```
PermissionError: [Errno 13] Permission denied: '/app/logs/monitor.log'
```

## 🔍 问题原因

Docker 容器内的用户权限与宿主机目录权限不匹配：

- **容器内**: `monitor` 用户 (UID:1000, GID:1000)
- **宿主机**: `./logs` 目录可能由 root 或其他用户创建
- **结果**: 容器内用户无法写入宿主机目录

---

## ✅ 解决方案（3选1）

### 方案 1: 预设目录权限（快速）⭐

**适用场景**: 快速测试、临时部署

#### 步骤 1: 运行设置脚本

```bash
# 使用提供的脚本（推荐）
sudo bash scripts/setup-logs.sh
```

或手动设置：

```bash
# 创建日志目录
mkdir -p logs

# 设置所有者为 UID 1000
sudo chown -R 1000:1000 logs

# 验证
ls -ld logs
# 输出应该是: drwxr-xr-x ... 1000 1000 ... logs
```

#### 步骤 2: 启动服务

```bash
docker-compose up -d
```

#### 优缺点

✅ **优点**:
- 快速简单
- 无需修改代码

❌ **缺点**:
- 需要 sudo 权限
- 每次重新创建 logs 目录都需要重新设置

---

### 方案 2: 自定义用户 UID/GID（推荐）⭐⭐⭐

**适用场景**: 生产环境、多用户环境

#### 步骤 1: 获取当前用户 UID/GID

```bash
echo "USER_ID=$(id -u)"
echo "GROUP_ID=$(id -g)"
```

输出示例：
```
USER_ID=1001
GROUP_ID=1001
```

#### 步骤 2: 配置环境变量

编辑 `.env` 文件：

```bash
cp .env.example .env
nano .env
```

添加或修改：

```bash
# 使用你的实际 UID/GID
USER_ID=1001
GROUP_ID=1001
```

#### 步骤 3: 构建并启动

```bash
# 重新构建镜像（使用自定义 UID/GID）
docker-compose build

# 启动服务
docker-compose up -d
```

#### 步骤 4: 验证

```bash
# 查看容器内用户
docker-compose exec dolphin-monitor id

# 应该输出类似:
# uid=1001(monitor) gid=1001(monitor) groups=1001(monitor)

# 检查日志文件权限
docker-compose exec dolphin-monitor ls -l /app/logs/
```

#### 优缺点

✅ **优点**:
- 与宿主机用户权限完全匹配
- 无需 sudo
- 适合生产环境

❌ **缺点**:
- 需要重新构建镜像
- 配置稍复杂

---

### 方案 3: 使用命名卷（最简单）⭐⭐

**适用场景**: 不需要直接访问日志文件

#### 步骤 1: 使用命名卷配置

```bash
# 使用命名卷版本的 compose 文件
docker-compose -f docker-compose.named-volume.yaml up -d
```

#### 步骤 2: 查看日志

```bash
# 通过 docker logs 查看
docker-compose logs -f dolphin-monitor

# 或进入容器查看
docker-compose exec dolphin-monitor cat /app/logs/monitor.log
```

#### 步骤 3: 访问日志文件（如需要）

```bash
# 找到卷的位置
docker volume inspect dolphin-workflow-monitor_monitor-logs

# 使用 sudo 访问
sudo ls /var/lib/docker/volumes/dolphin-workflow-monitor_monitor-logs/_data/
```

#### 优缺点

✅ **优点**:
- 无权限问题
- 无需配置
- Docker 自动管理

❌ **缺点**:
- 无法直接从宿主机访问日志文件
- 需要通过 docker 命令查看日志

---

## 📋 完整部署流程

### 使用方案 2（推荐）

```bash
# 1. 克隆项目
git clone <repository-url>
cd dolphin-workflow-monitor

# 2. 配置环境变量
cp .env.example .env

# 3. 编辑 .env，设置必要参数
nano .env
# 必须设置:
#   - DS_API_URL
#   - DS_TOKEN
#   - USER_ID (运行 id -u 获取)
#   - GROUP_ID (运行 id -g 获取)

# 4. 创建日志目录
mkdir -p logs

# 5. 构建镜像
docker-compose build

# 6. 启动服务
docker-compose up -d

# 7. 查看日志
docker-compose logs -f

# 8. 验证运行
docker-compose ps
```

---

## 🔧 故障排除

### 问题 1: 仍然提示权限错误

**检查步骤**:

```bash
# 1. 检查宿主机目录权限
ls -ld logs
# 应该匹配容器内的 UID/GID

# 2. 检查容器内用户
docker-compose exec dolphin-monitor id

# 3. 对比两者是否一致
```

**解决方法**:

```bash
# 如果不一致，重新设置目录权限
USER_ID=$(id -u)
sudo chown -R ${USER_ID}:${USER_ID} logs

# 或重新构建镜像
docker-compose build --no-cache
docker-compose up -d
```

### 问题 2: 无法使用 sudo

如果你的环境不允许使用 sudo：

**方案 A**: 使用命名卷（方案 3）

**方案 B**: 让管理员预先设置目录权限

```bash
# 管理员执行
sudo mkdir -p /path/to/project/logs
sudo chown -R YOUR_USER:YOUR_GROUP /path/to/project/logs
```

### 问题 3: SELinux 阻止访问

如果在 CentOS/RHEL 等系统上遇到 SELinux 问题：

```bash
# 临时解决
sudo setenforce 0

# 永久解决：添加 :z 标志到 volume 挂载
# 编辑 docker-compose.yaml:
volumes:
  - ./logs:/app/logs:z
```

---

## 📊 方案对比

| 特性 | 方案 1 | 方案 2 | 方案 3 |
|------|--------|--------|--------|
| 配置复杂度 | ⭐ 简单 | ⭐⭐ 中等 | ⭐ 简单 |
| 需要 sudo | ✅ 是 | ❌ 否 | ❌ 否 |
| 直接访问日志 | ✅ 是 | ✅ 是 | ❌ 否 |
| 适合生产环境 | ⚠️ 一般 | ✅ 是 | ✅ 是 |
| 跨平台兼容性 | ⚠️ 一般 | ✅ 好 | ✅ 好 |

---

## 🚀 生产环境建议

1. **使用方案 2**：自定义 UID/GID
2. **配置日志轮转**：避免日志文件过大
3. **监控磁盘空间**：定期清理旧日志
4. **备份配置文件**：`.env` 文件包含敏感信息

### 日志轮转配置

创建 `docker-compose.override.yaml`:

```yaml
version: '3.8'

services:
  dolphin-monitor:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

启动时会自动合并配置：

```bash
docker-compose up -d
```

---

## 📝 测试清单

部署后验证：

- [ ] 容器成功启动 (`docker-compose ps`)
- [ ] 日志文件被创建 (`ls -l logs/`)
- [ ] 没有权限错误 (`docker-compose logs`)
- [ ] 监控功能正常 (检查日志内容)
- [ ] 能够正常写入日志

---

## 💡 额外技巧

### 快速查看日志

```bash
# 实时查看
docker-compose logs -f

# 查看最近 100 行
docker-compose logs --tail=100

# 查看特定时间的日志
docker-compose logs --since 30m
```

### 进入容器调试

```bash
# 进入容器
docker-compose exec dolphin-monitor bash

# 检查权限
ls -la /app/logs/

# 手动测试写入
echo "test" > /app/logs/test.log
```

### 清理和重置

```bash
# 停止并删除容器
docker-compose down

# 清理日志（小心！会删除所有日志）
sudo rm -rf logs/*

# 重新开始
docker-compose up -d
```

---

## 🆘 需要帮助？

如果以上方案都无法解决你的问题：

1. 收集诊断信息：
   ```bash
   # 系统信息
   uname -a
   docker --version
   docker-compose --version

   # 权限信息
   id
   ls -ld logs/

   # 容器信息
   docker-compose ps
   docker-compose logs --tail=50
   ```

2. 提交 Issue 并附上诊断信息
3. 描述你的环境（OS、Docker 版本、是否使用 sudo 等）

---

## 🔒 安全建议

1. **不要使用 777 权限**：虽然能解决问题，但不安全
2. **定期更新镜像**：保持依赖包最新
3. **限制容器权限**：不要给予不必要的权限
4. **保护敏感信息**：`.env` 文件不要提交到 Git

---

## 📚 相关文档

- [Docker 官方文档 - 卷管理](https://docs.docker.com/storage/volumes/)
- [Docker Compose 文档](https://docs.docker.com/compose/)
- [Linux 文件权限](https://www.linux.com/training-tutorials/understanding-linux-file-permissions/)
