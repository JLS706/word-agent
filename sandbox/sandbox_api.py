# -*- coding: utf-8 -*-
"""
DocMaster - Sandbox Service (沙盒微服务)

这是一个独立的 FastAPI 服务，为 Agent 提供安全的代码执行环境。
沙盒引擎统一使用 core/sandbox.py，本文件只是 HTTP 包装层。

端点：
  POST /execute    — 严格模式（只读分析）
  POST /test-tool  — 工具模式（允许 os/subprocess，测试自定义工具代码）
  GET  /health     — 健康检查
"""

from fastapi import FastAPI
from pydantic import BaseModel

from core.sandbox import execute_sandboxed, test_tool_sandboxed


# ─────────────────────────────────────────────
# FastAPI 应用
# ─────────────────────────────────────────────

app = FastAPI(
    title="DocMaster Sandbox Service",
    description="安全代码执行沙盒微服务（引擎: core/sandbox.py）",
    version="2.0.0",
)


# ─────────────────────────────────────────────
# 请求 / 响应模型
# ─────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    """代码执行请求"""
    code: str
    timeout: int = 5

class ExecuteResponse(BaseModel):
    """代码执行结果"""
    success: bool
    output: str
    error: str = ""

class TestToolRequest(BaseModel):
    """工具代码测试请求"""
    code: str
    timeout: int = 10

class TestToolResponse(BaseModel):
    """工具代码测试结果"""
    success: bool
    output: str
    error: str = ""
    test_output: str = ""


# ─────────────────────────────────────────────
# API 路由
# ─────────────────────────────────────────────

@app.get("/health")
def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "sandbox", "version": "2.0.0"}


@app.post("/execute", response_model=ExecuteResponse)
def execute_code(req: ExecuteRequest):
    """
    严格模式：执行只读 Python 代码（白名单: re, math, json 等）。

    示例:
        POST /execute
        {"code": "print(1 + 1)", "timeout": 5}
    """
    result = execute_sandboxed(req.code, timeout=req.timeout)
    # execute_sandboxed 返回的是格式化的字符串
    has_error = result.startswith("❌") or result.startswith("代码安全检查未通过")
    return ExecuteResponse(
        success=not has_error,
        output=result if not has_error else "",
        error=result if has_error else "",
    )


@app.post("/test-tool", response_model=TestToolResponse)
def test_tool_code(req: TestToolRequest):
    """
    工具模式：测试自定义工具代码（白名单更宽，允许 os/subprocess 等）。

    示例:
        POST /test-tool
        {"code": "from tools.base import Tool\\nclass CustomTool(Tool): ...", "timeout": 10}
    """
    result = test_tool_sandboxed(req.code, timeout=req.timeout)
    return TestToolResponse(
        success=result.get("success", False),
        output=result.get("stdout", ""),
        error=result.get("error", ""),
        test_output=result.get("test_output", ""),
    )


# ─────────────────────────────────────────────
# 启动入口
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 40)
    print("  Sandbox Service v2.0")
    print("  http://localhost:8001")
    print("  Engine: core/sandbox.py")
    print("  Endpoints: /execute, /test-tool")
    print("=" * 40)
    uvicorn.run(app, host="0.0.0.0", port=8001)
