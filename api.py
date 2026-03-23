# -*- coding: utf-8 -*-
"""
DocMaster Agent — FastAPI Web 接口层

将命令行 Agent 包装为 HTTP 服务，为后续 Docker 容器化做准备。

启动方式:
    python api.py

接口:
    POST /chat    — 对话（发送指令，获取 Agent 回复）
    GET  /health  — 健康检查
    GET  /tools   — 查看可用工具列表
"""

import os
import sys
import toml
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.llm import LLM
from core.agent import Agent
from tools.base import ToolRegistry


# ─────────────────────────────────────────────
# 全局单例（在 lifespan 中初始化）
# ─────────────────────────────────────────────

agent_instance: Agent | None = None
tool_registry: ToolRegistry | None = None


def _load_config() -> dict:
    """加载配置文件"""
    config_path = os.path.join(os.path.dirname(__file__), "config", "config.toml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"配置文件不存在: {config_path}\n"
            f"请复制 config/config.example.toml → config/config.toml 并填入 API Key"
        )
    return toml.load(config_path)


def _create_agent(config: dict) -> tuple[Agent, ToolRegistry]:
    """根据配置创建 Agent 和工具注册表"""
    # 创建 LLM
    llm_config = config.get("llm", {})
    llm = LLM(
        api_key=llm_config.get("api_key", ""),
        model=llm_config.get("model", "gpt-4o-mini"),
        base_url=llm_config.get("base_url"),
    )

    # 创建工具注册表
    registry = ToolRegistry()

    # 动态导入并注册所有工具（与 main.py 保持一致）
    from tools.ref_formatter import RefFormatterTool
    from tools.ref_crossref import RefCrossRefTool
    from tools.fig_crossref import FigCrossRefTool
    from tools.fig_caption import FigCaptionTool
    from tools.acronym_checker import AcronymCheckerTool
    from tools.latex_converter import LatexConverterTool
    from tools.code_interpreter import CodeInterpreterTool
    from tools.learned_rules import (
        SaveLearnedRuleTool, ForgetLearnedRuleTool, ListLearnedRulesTool
    )
    from tools.rag import IndexDocumentTool, SearchDocumentTool

    all_tools = [
        RefFormatterTool, RefCrossRefTool, FigCrossRefTool,
        FigCaptionTool, AcronymCheckerTool, LatexConverterTool,
        CodeInterpreterTool, SaveLearnedRuleTool,
        ForgetLearnedRuleTool, ListLearnedRulesTool,
        IndexDocumentTool, SearchDocumentTool,
    ]
    for tool_cls in all_tools:
        registry.register(tool_cls())

    # 创建 Agent
    agent_config = config.get("agent", {})
    agent = Agent(
        llm=llm,
        tool_registry=registry,
        max_steps=agent_config.get("max_steps", 10),
        verbose=True,
    )

    return agent, registry


# ─────────────────────────────────────────────
# FastAPI 应用生命周期
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用启动时初始化 Agent，关闭时清理资源。
    这是 FastAPI 推荐的初始化方式（替代 @app.on_event）。
    """
    global agent_instance, tool_registry

    print("[*] 正在初始化 Agent...")
    config = _load_config()
    agent_instance, tool_registry = _create_agent(config)
    print(f"[OK] Agent 就绪，已加载 {len(tool_registry.get_all_tools())} 个工具")

    yield  # ← 应用运行中

    print("[*] Agent 服务关闭")


app = FastAPI(
    title="DocMaster Agent API",
    description="学术论文排版 AI 智能助手 — HTTP 接口",
    version="1.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
# 请求/响应数据模型
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    """对话请求"""
    message: str

    model_config = {
        "json_schema_extra": {
            "examples": [{"message": "帮我检查一下缩写有没有定义"}]
        }
    }


class ChatResponse(BaseModel):
    """对话回复"""
    reply: str
    success: bool


class ToolInfo(BaseModel):
    """工具信息"""
    name: str
    description: str
    parameters: list[str]


# ─────────────────────────────────────────────
# API 路由
# ─────────────────────────────────────────────

@app.get("/health")
def health_check():
    """
    健康检查 — Docker/K8s 用这个接口判断服务是否存活。
    """
    return {
        "status": "healthy",
        "agent_ready": agent_instance is not None,
    }


@app.get("/tools", response_model=list[ToolInfo])
def list_tools():
    """
    查看所有可用工具。
    """
    if tool_registry is None:
        return []
    tools = []
    for t in tool_registry.get_all_tools():
        params = list(t.parameters.get("properties", {}).keys())
        tools.append(ToolInfo(
            name=t.name,
            description=t.description[:100],
            parameters=params,
        ))
    return tools


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    对话接口 — 发送自然语言指令，Agent 执行后返回结果。

    示例请求:
        POST /chat
        {"message": "帮我格式化参考文献"}
    """
    if agent_instance is None:
        return ChatResponse(reply="Agent 未初始化", success=False)

    try:
        # 重置 Agent（每次请求是独立的对话）
        agent_instance.reset()
        reply = agent_instance.run(req.message)
        return ChatResponse(reply=reply, success=True)
    except Exception as e:
        return ChatResponse(reply=f"Agent 执行失败: {e}", success=False)


# ─────────────────────────────────────────────
# 直接运行入口
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  DocMaster Agent API Server")
    print("  http://localhost:8000")
    print("  http://localhost:8000/docs  <- API 文档")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)
