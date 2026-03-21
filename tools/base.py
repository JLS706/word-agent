# -*- coding: utf-8 -*-
"""
DocMaster Agent - Tool 基类
所有工具插件的统一抽象接口。
"""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """工具基类，所有 Tool 插件必须继承此类。"""

    # 子类必须覆盖这三个属性
    name: str = ""
    description: str = ""
    parameters: dict = {}  # JSON Schema 格式

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """
        执行工具逻辑。

        Args:
            **kwargs: 由 LLM 根据 parameters 定义传入的参数

        Returns:
            执行结果的文本描述
        """
        ...

    def to_openai_tool(self) -> dict:
        """转为 OpenAI function calling 格式的工具定义"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"<Tool: {self.name}>"


class ToolRegistry:
    """工具注册表，管理所有可用工具"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具"""
        if not tool.name:
            raise ValueError(f"工具 {tool.__class__.__name__} 缺少 name 属性")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """根据名称获取工具"""
        return self._tools.get(name)

    def get_all_tools(self) -> list[Tool]:
        """获取所有已注册工具"""
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict]:
        """将所有工具转为 OpenAI function calling 格式"""
        return [tool.to_openai_tool() for tool in self._tools.values()]

    def describe(self) -> str:
        """生成所有工具的文字描述（用于日志/调试）"""
        lines = []
        for tool in self._tools.values():
            lines.append(f"  - {tool.name}: {tool.description}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._tools)
