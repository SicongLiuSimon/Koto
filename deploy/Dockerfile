# ===== Koto AI Assistant - Docker Image =====
# 多阶段构建，生产优化

FROM python:3.11-slim AS base

# 系统依赖（文档处理需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖文件（利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY web/ web/
COPY app/ app/
COPY server.py .
COPY config/gemini_config.env.example config/gemini_config.env

# 创建必要目录
RUN mkdir -p logs chats workspace config

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV KOTO_PORT=5000
ENV KOTO_AUTH_ENABLED=true
ENV KOTO_DEPLOY_MODE=cloud

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/api/ping')" || exit 1

EXPOSE 5000

# 生产启动（gunicorn）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "120", "server:app"]
