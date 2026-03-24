# 沙盒微服务的 Dockerfile
# 构建上下文是项目根目录，可以访问 core/ 模块

FROM python:3.11-slim
WORKDIR /app

# 安装依赖（沙盒只需要 fastapi + uvicorn）
COPY sandbox/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制沙盒引擎和 API
COPY core/sandbox.py core/sandbox.py
COPY core/__init__.py core/__init__.py
COPY sandbox/sandbox_api.py .

EXPOSE 8001

CMD ["python", "sandbox_api.py"]
