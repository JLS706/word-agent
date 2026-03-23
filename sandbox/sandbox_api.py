# -*- coding: utf-8 -*-
"""
DocMaster - Sandbox Service (独立沙盒微服务)

这是一个独立的 FastAPI 服务，只做一件事：接收 Python 代码，在沙盒中执行，返回结果。
它将被打包成独立的 Docker 容器，与 Agent 容器隔离运行。

为什么要独立？
  - 代码炸了只影响这个容器，Agent 容器不受影响
  - 可以给这个容器单独设资源限制（CPU/内存上限）
  - 需要扩容时，只复制这个容器就行
"""

import ast
import sys
import io
import multiprocessing
from contextlib import redirect_stdout

from fastapi import FastAPI
from pydantic import BaseModel


# ─────────────────────────────────────────────
# FastAPI 应用
# ─────────────────────────────────────────────

app = FastAPI(
    title="DocMaster Sandbox Service",
    description="安全代码执行沙盒微服务",
    version="1.0.0",
)


# ─────────────────────────────────────────────
# 请求/响应模型
# ─────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    """代码执行请求"""
    code: str
    timeout: int = 5  # 超时秒数，默认5秒

class ExecuteResponse(BaseModel):
    """代码执行结果"""
    success: bool
    output: str
    error: str = ""


# ─────────────────────────────────────────────
# 沙盒核心（从 code_interpreter.py 提取的精简版）
# ─────────────────────────────────────────────

# AST 安全检查白名单
SAFE_MODULES = {"math", "re", "json", "random", "datetime", "collections", "itertools", "functools", "string"}
DANGEROUS_CALLS = {"exec", "eval", "compile", "__import__", "globals", "locals", "getattr", "setattr", "delattr"}

def _check_ast_safety(code: str) -> str | None:
    """Layer 1: AST 静态分析 — 在执行前扫描危险模式"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    for node in ast.walk(tree):
        # 检查危险 import
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in SAFE_MODULES:
                    return f"import {alias.name} is not allowed"
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] not in SAFE_MODULES:
                return f"from {node.module} import ... is not allowed"
        # 检查危险函数调用
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in DANGEROUS_CALLS:
                return f"{node.func.id}() is not allowed"
    return None


def _sandbox_worker(code: str, result_queue):
    """子进程入口：在受限环境中执行代码"""
    import builtins as _builtins

    # Layer 2: 构造安全 builtins
    safe_names = [
        "print", "len", "range", "int", "float", "str", "list", "dict",
        "tuple", "set", "bool", "abs", "max", "min", "sum", "sorted",
        "enumerate", "zip", "map", "filter", "isinstance", "type",
        "round", "reversed", "any", "all", "chr", "ord", "hex",
    ]
    safe_builtins = {name: getattr(_builtins, name) for name in safe_names if hasattr(_builtins, name)}
    safe_builtins["__build_class__"] = _builtins.__build_class__

    restricted_globals = {
        "__builtins__": safe_builtins,
        "__name__": "__sandbox__",
    }

    # 预加载安全模块
    import math, re
    restricted_globals["math"] = math
    restricted_globals["re"] = re

    # 捕获 stdout
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(code, "<sandbox>", "exec"), restricted_globals)
        result_queue.put({"output": buf.getvalue(), "error": ""})
    except Exception as e:
        result_queue.put({"output": buf.getvalue(), "error": f"{type(e).__name__}: {e}"})


def execute_in_sandbox(code: str, timeout: int = 5) -> dict:
    """完整的三层沙盒执行"""
    # Layer 1: AST 检查
    error = _check_ast_safety(code)
    if error:
        return {"output": "", "error": f"[AST] {error}"}

    # Layer 2+3: 子进程执行
    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_sandbox_worker, args=(code, queue), daemon=True)
    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
        return {"output": "", "error": f"[TIMEOUT] code execution exceeded {timeout}s limit"}

    if not queue.empty():
        return queue.get_nowait()
    return {"output": "", "error": "[ERROR] no result from sandbox"}


# ─────────────────────────────────────────────
# API 路由
# ─────────────────────────────────────────────

@app.get("/health")
def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "sandbox"}


@app.post("/execute", response_model=ExecuteResponse)
def execute_code(req: ExecuteRequest):
    """
    执行 Python 代码。

    示例:
        POST /execute
        {"code": "print(1 + 1)", "timeout": 5}
    """
    result = execute_in_sandbox(req.code, req.timeout)
    return ExecuteResponse(
        success=not bool(result["error"]),
        output=result["output"],
        error=result["error"],
    )


# ─────────────────────────────────────────────
# 启动入口
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 40)
    print("  Sandbox Service")
    print("  http://localhost:8001")
    print("=" * 40)
    uvicorn.run(app, host="0.0.0.0", port=8001)
