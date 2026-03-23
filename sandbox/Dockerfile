# 沙盒微服务的 Dockerfile
# 跟主服务的 Dockerfile 套路一样，只是跑不同的 .py 文件

FROM python:3.11-slim
WORKDIR /app

# 沙盒只需要 fastapi 和 uvicorn，不需要 openai 等
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sandbox_api.py .

EXPOSE 8001

CMD ["python", "sandbox_api.py"]
