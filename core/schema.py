# -*- coding: utf-8 -*-
"""
DocMaster Agent - 数据模型定义
定义 Agent 运行所需的核心数据结构。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union

@dataclass
class StreamEvent:
    """
    Agent 向外发射的流式事件契约。

    事件类型 (type):
        - "text"           : LLM 思考的文本增量（打字机效果）
        - "tool_start"     : 工具开始执行（metadata 含 tool, args）
        - "tool_progress"  : 工具执行中的进度更新（metadata 含 percent, tool）
        - "tool_end"       : 工具执行完毕（metadata 含 success, tool）
        - "tool_timeout"   : 工具心跳停滞熔断（metadata 含 stall_seconds, killed_pids, tool）
        - "error"          : 发生错误
        - "finish"         : 本轮任务结束

    上层消费者（终端 / WebSocket / IDE Bridge）只需 switch-case 这些类型即可。
    """
    type: str              # 事件类型
    content: str           # 文本内容或状态描述
    metadata: dict = field(default_factory=dict) # 携带的额外结构化数据（如 tool_name）


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
    """
    对话消息。

    content 支持两种格式：
      - str：纯文本消息（传统模式）
      - list[dict]：多模态消息（OpenAI Vision 格式）
        例: [{"type": "text", "text": "..."},
              {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
    """
    role: Role
    content: Optional[Union[str, list[dict]]] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None  # 仅 role=TOOL 时使用
    name: Optional[str] = None          # 仅 role=TOOL 时使用

    @property
    def text_content(self) -> str:
        """始终返回纯文本部分（兼容 str 和 list[dict] 两种格式）。"""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        # 多模态格式：拼接所有 text 部分
        return "".join(
            part.get("text", "") for part in self.content
            if isinstance(part, dict) and part.get("type") == "text"
        )

    @staticmethod
    def with_images(
        role: "Role",
        text: str,
        image_data: list[str],
        detail: str = "auto",
    ) -> "Message":
        """
        创建带图片的多模态消息。

        Args:
            role: 消息角色
            text: 文本内容
            image_data: base64 编码的图片列表（或 URL）
            detail: 图片解析度 ("auto" / "low" / "high")
        """
        parts: list[dict] = [{"type": "text", "text": text}]
        for img in image_data:
            if img.startswith(("http://", "https://")):
                url = img
            else:
                url = f"data:image/png;base64,{img}"
            parts.append({
                "type": "image_url",
                "image_url": {"url": url, "detail": detail},
            })
        return Message(role=role, content=parts)

    def to_dict(self) -> dict:
        """转为 OpenAI API 兼容的字典格式（自动兼容多模态）"""
        msg = {"role": self.role.value}

        if self.content is not None:
            msg["content"] = self.content  # str 或 list[dict] 均直接透传

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
