# DolphinScheduler 工作流监控器 Dockerfile

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源代码
COPY src/ ./src/
COPY config/ ./config/
COPY main.py .

# 创建日志目录
RUN mkdir -p /app/logs

# 设置非 root 用户
# 使用 ARG 允许自定义 UID/GID
ARG USER_ID=1000
ARG GROUP_ID=1000

RUN groupadd -g ${GROUP_ID} monitor && \
    useradd -m -u ${USER_ID} -g ${GROUP_ID} monitor && \
    chown -R monitor:monitor /app && \
    chmod -R 755 /app/logs

USER monitor

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health', timeout=5)" || exit 1

# 入口点
ENTRYPOINT ["python", "main.py"]
CMD ["run"]
