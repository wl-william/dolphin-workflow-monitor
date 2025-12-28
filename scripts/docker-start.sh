#!/bin/bash
#
# Docker 方式启动 DolphinScheduler 工作流监控器
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "创建 .env 文件..."
    cp .env.example .env
    echo ""
    echo "请编辑 .env 文件，配置以下必要参数:"
    echo "  - DS_API_URL: DolphinScheduler API 地址"
    echo "  - DS_TOKEN: DolphinScheduler 认证 Token"
    echo ""
    exit 1
fi

# 检查 DS_TOKEN
source .env
if [ -z "$DS_TOKEN" ] || [ "$DS_TOKEN" = "your_token_here" ]; then
    echo "错误: 请在 .env 文件中配置有效的 DS_TOKEN"
    exit 1
fi

# 创建日志目录
mkdir -p logs

# 构建并启动
echo "构建 Docker 镜像..."
docker-compose build

echo "启动容器..."
docker-compose up -d

echo ""
echo "监控器已启动！"
echo ""
echo "查看日志: docker-compose logs -f"
echo "停止服务: docker-compose down"
echo ""
