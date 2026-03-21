# -*- coding: utf-8 -*-
"""
DocMaster Agent - LLM 接口封装
统一封装 LLM 调用，兼容所有 OpenAI SDK 兼容的 API 平台：
  - Google Gemini (generativelanguage.googleapis.com)
  - 智谱 GLM (open.bigmodel.cn)
  - DeepSeek (api.deepseek.com)
  - 硅基流动 SiliconFlow (api.siliconflow.cn)
  - 通义千问 via 阿里云百炼
  - 任何 OpenAI 兼容 API
"""

import json
from typing import Optional

from core.schema import Message, Role, ToolCall


class LLM:
    """大语言模型调用封装"""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "请安装 openai 库: pip install openai"
            )

        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
    ) -> Message:
        """
        发送对话消息给 LLM，返回响应 Message。

        Args:
            messages: 对话历史
            tools: 可用工具列表（OpenAI function calling 格式）

        Returns:
            LLM 回复的 Message（可能包含 tool_calls 或纯文本 content）
        """
        # 构建请求参数
        api_messages = [m.to_dict() for m in messages]
        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # 调用 API（带自动重试，应对 429 限流）
        import time
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries:
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s
                    print(f"   [LLM] 触发限流，{wait}秒后自动重试 ({attempt+1}/{max_retries})...")
                    time.sleep(wait)
                else:
                    raise

        choice = response.choices[0]
        resp_msg = choice.message

        # 解析工具调用
        tool_calls = []
        if resp_msg.tool_calls:
            for tc in resp_msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {"raw": tc.function.arguments}

                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        return Message(
            role=Role.ASSISTANT,
            content=resp_msg.content,
            tool_calls=tool_calls,
        )

    def test_connection(self) -> str:
        """测试 LLM 连通性，返回模型回复"""
        msg = Message(role=Role.USER, content="你好，请回复'连接成功'四个字。")
        resp = self.chat([msg])
        return resp.content or "(无回复)"
