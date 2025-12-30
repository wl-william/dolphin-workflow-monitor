# 快速参考 - Docker 部署配置

## 📋 一键部署命令

```bash
# 1. 克隆项目
git clone <repository-url>
cd dolphin-workflow-monitor

# 2. 配置环境
cp .env.example .env
echo "USER_ID=$(id -u)" >> .env
echo "GROUP_ID=$(id -g)" >> .env

# 3. 配置 DolphinScheduler 服务器 IP
# 方法 A: 如果在宿主机
echo "DS_HOST_IP=172.17.0.1" >> .env

# 方法 B: 如果在其他服务器（替换为实际 IP）
# echo "DS_HOST_IP=192.168.1.100" >> .env

# 4. 编辑配置填写 Token
nano .env

# 5. 启动
docker-compose build && docker-compose up -d

# 6. 查看日志
docker-compose logs -f
```

## 🔧 必需配置项

### .env 文件配置

```bash
# ========== 必填 ==========
# DolphinScheduler API 地址
DS_API_URL=http://dolphinscheuler.master2.com:12345/dolphinscheduler

# API Token（在 DolphinScheduler 安全中心生成）
DS_TOKEN=your_token_here

# DolphinScheduler 服务器 IP（用于 Docker host 映射）
DS_HOST_IP=172.17.0.1  # 或实际服务器 IP

# ========== 权限配置 ==========
# 用户 ID 和组 ID（避免权限问题）
USER_ID=1000
GROUP_ID=1000

# ========== 可选配置 ==========
DS_CHECK_INTERVAL=60
DS_CONTINUOUS_MODE=true
DS_AUTO_RECOVERY=true
DS_MAX_RECOVERY_ATTEMPTS=3
DS_TIME_WINDOW_HOURS=24  # 只监控指定小时内启动的工作流
DS_MAX_FAILURES_FOR_RECOVERY=1  # 时间窗口内失败数量阈值，超过只通知不恢复
DS_LOG_LEVEL=INFO
```

## 🌐 Host 映射说明

### 什么是 Host 映射？

Docker 容器有自己的网络环境，无法直接解析宿主机或内网的主机名。`extra_hosts` 配置可以在容器的 `/etc/hosts` 文件中添加主机名到 IP 的映射。

### 获取服务器 IP 的方法

#### 方法 1: DolphinScheduler 在宿主机上

```bash
# 获取 Docker 默认网关（宿主机 IP）
ip route show default | awk '/default/ {print $3}'
# 输出通常是: 172.17.0.1
```

#### 方法 2: DolphinScheduler 在其他服务器

```bash
# 使用 ping
ping dolphinscheuler.master2.com
# 输出: PING dolphinscheuler.master2.com (192.168.1.100) ...

# 使用 nslookup
nslookup dolphinscheuler.master2.com
# 输出: Address: 192.168.1.100

# 使用 host
host dolphinscheuler.master2.com
# 输出: dolphinscheuler.master2.com has address 192.168.1.100
```

#### 方法 3: 从宿主机 /etc/hosts 查看

```bash
grep dolphinscheuler.master2.com /etc/hosts
# 输出: 192.168.1.100 dolphinscheuler.master2.com
```

### docker-compose.yaml 配置

```yaml
services:
  dolphin-monitor:
    # ... 其他配置 ...
    extra_hosts:
      # 主机名:IP 映射
      - "dolphinscheuler.master2.com:${DS_HOST_IP:-172.17.0.1}"
      # 可以添加多个
      # - "another.host:192.168.1.101"
```

## 🧪 验证配置

### 1. 验证容器启动

```bash
docker-compose ps
# 应显示 "Up" 状态
```

### 2. 验证主机名解析

```bash
# 进入容器
docker-compose exec dolphin-monitor bash

# 查看 /etc/hosts
cat /etc/hosts
# 应包含: 192.168.1.100 dolphinscheuler.master2.com

# 测试 ping
ping -c 3 dolphinscheuler.master2.com
# 应有响应

# 退出容器
exit
```

### 3. 验证 API 连接

```bash
# 进入容器
docker-compose exec dolphin-monitor bash

# 测试 DolphinScheduler API
curl -v http://dolphinscheuler.master2.com:12345/dolphinscheduler
# 应返回 HTML 或 JSON 响应

# 退出容器
exit
```

### 4. 查看应用日志

```bash
# 查看实时日志
docker-compose logs -f

# 应看到类似输出:
# dolphin-workflow-monitor | INFO - Starting workflow monitoring...
# dolphin-workflow-monitor | INFO - Connected to DolphinScheduler at http://dolphinscheuler.master2.com:12345
```

## 🐛 故障排查

### 问题 1: 权限错误

```bash
# 错误: Permission denied: '/app/logs/monitor.log'
# 解决:
sudo bash scripts/setup-logs.sh
docker-compose restart
```

### 问题 2: 无法解析主机名

```bash
# 错误: ConnectionError: Failed to establish a connection
# 检查:
docker-compose exec dolphin-monitor ping dolphinscheuler.master2.com

# 如果失败，检查 DS_HOST_IP 是否正确
# 修改 .env 后重启:
docker-compose down
docker-compose up -d
```

### 问题 3: 连接超时

```bash
# 检查防火墙
telnet dolphinscheuler.master2.com 12345

# 检查 DolphinScheduler 服务
curl http://dolphinscheuler.master2.com:12345/dolphinscheduler

# 如果宿主机可以访问但容器不行，检查 extra_hosts 配置
```

### 问题 4: Token 无效

```bash
# 错误: Token verification failed
# 解决:
# 1. 重新生成 Token（在 DolphinScheduler 安全中心）
# 2. 更新 .env 文件
# 3. 重启容器
docker-compose restart
```

## 📊 常用命令

```bash
# 启动服务
docker-compose up -d

# 停止服务
docker-compose down

# 重启服务
docker-compose restart

# 查看日志（实时）
docker-compose logs -f

# 查看最近 100 行日志
docker-compose logs --tail=100

# 查看容器状态
docker-compose ps

# 进入容器
docker-compose exec dolphin-monitor bash

# 重新构建镜像
docker-compose build --no-cache

# 查看容器资源使用
docker stats dolphin-workflow-monitor
```

## 🔐 安全建议

1. **保护 Token**
   ```bash
   # .env 文件权限
   chmod 600 .env
   ```

2. **不要提交敏感信息**
   ```bash
   # .gitignore 中已包含
   .env
   config.yaml
   ```

3. **定期更新 Token**
   - 设置 Token 过期时间
   - 定期轮换 Token

4. **限制网络访问**
   - 使用防火墙限制容器访问范围
   - 只开放必要的端口

## 📚 完整文档

- [README.md](README.md) - 完整使用指南
- [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) - 详细部署文档
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - 本文档

## 🎯 快速配置模板

### .env 文件模板（复制并修改）

```bash
# DolphinScheduler Configuration
DS_API_URL=http://dolphinscheuler.master2.com:12345/dolphinscheduler
DS_TOKEN=<在这里粘贴你的Token>
DS_HOST_IP=<在这里填写IP>

# Docker User Permissions
USER_ID=1000
GROUP_ID=1000

# Monitor Configuration
DS_CHECK_INTERVAL=60
DS_CONTINUOUS_MODE=true
DS_AUTO_RECOVERY=true
DS_MAX_RECOVERY_ATTEMPTS=3
DS_TIME_WINDOW_HOURS=24  # 只监控指定小时内启动的工作流
DS_MAX_FAILURES_FOR_RECOVERY=1  # 时间窗口内失败数量阈值，超过只通知不恢复
DS_LOG_LEVEL=INFO
```

### 配置步骤检查清单

- [ ] 克隆项目
- [ ] 复制 `.env.example` 到 `.env`
- [ ] 设置 `USER_ID` 和 `GROUP_ID`
- [ ] 获取并设置 `DS_HOST_IP`
- [ ] 在 DolphinScheduler 生成 Token
- [ ] 设置 `DS_TOKEN`
- [ ] 运行 `docker-compose build`
- [ ] 运行 `docker-compose up -d`
- [ ] 验证主机名解析
- [ ] 查看日志确认运行正常
