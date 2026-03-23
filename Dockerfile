# ──────────────────────────────────────────────
# DocMaster Agent — Dockerfile
# ──────────────────────────────────────────────
# 这个文件告诉 Docker：怎么把你的项目"打包"成一个镜像。
# 你可以把它理解为一份"装机清单"。

# 第 1 步：选一个基础环境（"先装操作系统"）
# python:3.11-slim 是一个精简的 Linux + Python 3.11 环境
# slim 版本比完整版小很多（约 150MB vs 900MB）
FROM python:3.11-slim

# 第 2 步：在容器里创建一个工作目录（"建一个文件夹"）
# 之后所有命令都在 /app 目录下执行
WORKDIR /app

# 第 3 步：先复制依赖清单并安装
# 用 requirements-docker.txt（排除了 Windows 专属的 pywin32）
# 为什么不直接复制所有文件？因为 Docker 有"层缓存"机制：
#   - 如果这个文件没变，这一层会被缓存，不重新安装
#   - 这样改代码时不用每次都重装依赖，构建速度快很多
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# 第 4 步：复制项目所有文件到容器里（"把代码搬进去"）
COPY . .

# 第 5 步：声明服务端口（"告诉外面我在监听 8000 端口"）
# 这只是一个文档性声明，实际端口映射在 docker run -p 时指定
EXPOSE 8000

# 第 6 步：容器启动时执行的命令（"开机自动运行"）
# 相当于在容器里执行: python api.py
CMD ["python", "api.py"]
