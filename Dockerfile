# 使用官方 Python 3.11 轻量级镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
# PYTHONUNBUFFERED=1: 让Python输出直接打印到终端，不缓冲（方便查看日志）
# PYTHONDONTWRITEBYTECODE=1: 防止生成.pyc字节码文件
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖（如果需要）
# 这里安装一些常用工具，curl用于健康检查
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
# 使用国内镜像源加速（可选）
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码
COPY backend/ /app/backend/

# 暴露端口（文档作用，实际映射在docker-compose中配置）
EXPOSE 8000

# 健康检查
# 每30秒检查一次服务是否正常，超时3秒，失败3次标记为不健康
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
# 使用 uvicorn 启动 FastAPI 应用
# --host 0.0.0.0: 监听所有网络接口
# --port 8000: 监听8000端口
# --workers 2: 启动2个工作进程（根据CPU核心数，2核建议2个）
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
