#!/bin/bash
# 设置日志目录权限

set -e

# 创建日志目录
mkdir -p logs

# 设置权限，允许容器内的用户（UID 1000）写入
# 方法 1: 修改所有者为 UID 1000
sudo chown -R 1000:1000 logs

# 或者方法 2: 设置宽松权限（不推荐生产环境）
# chmod -R 777 logs

echo "✅ 日志目录权限已设置"
echo "目录信息："
ls -ld logs
