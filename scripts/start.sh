#!/bin/bash
#
# DolphinScheduler 工作流监控器启动脚本
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 Python3，请先安装 Python 3.8+"
    exit 1
fi

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
if [ ! -f "venv/.deps_installed" ]; then
    echo "安装依赖..."
    pip install -r requirements.txt
    touch venv/.deps_installed
fi

# 检查环境变量
if [ -z "$DS_TOKEN" ]; then
    if [ -f ".env" ]; then
        source .env
    else
        echo "警告: 未设置 DS_TOKEN 环境变量"
        echo "请创建 .env 文件或设置环境变量"
        exit 1
    fi
fi

# 启动监控
echo "启动 DolphinScheduler 工作流监控器..."
python main.py run "$@"
