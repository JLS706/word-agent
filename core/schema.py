# -*- coding: utf-8 -*-
"""
DocMaster Agent - 数据模型定义
定义 Agent 运行所需的核心数据结构。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AgentState(Enum):
    """Agent 运行状态"""
    IDLE = "idle"           # 空闲，等待用户输入
    THINKING = "thinking"   # 正在调用 LLM 推理
    ACTING = "acting"       # 正在执行工具
    FINISHED = "finished"   # 本轮任务完成
    ERROR = "error"         # 发生错误


class Role(Enum):
    """消息角色"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """LLM 返回的工具调用请求"""
    id: str                     # 调用 ID（LLM 分配）
    name: str                   # 工具名称
    arguments: dict[str, Any]   # 工具参数


@dataclass
class ToolResult:
    """工具执行结果"""
    tool_call_id: str   # 对应的 ToolCall.id
    name: str           # 工具名称
    output: str         # 执行结果（文本）
    success: bool = True


@dataclass
class Message:
    """对话消息"""
    role: Role
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None  # 仅 role=TOOL 时使用
    name: Optional[str] = None          # 仅 role=TOOL 时使用

    def to_dict(self) -> dict:
        """转为 OpenAI API 兼容的字典格式"""
        msg = {"role": self.role.value}

        if self.content is not None:
            msg["content"] = self.content

        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": _safe_json_dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]

        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id

        if self.name is not None:
            msg["name"] = self.name

        return msg


def _safe_json_dumps(obj: Any) -> str:
    """安全地将对象序列化为 JSON 字符串"""
    import json
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False)
