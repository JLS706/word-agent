# -*- coding: utf-8 -*-
"""
DocMaster Agent - Tool 基类
所有工具插件的统一抽象接口。
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

# 进度回调签名: (percent: int, message: str) -> None
# 由异步引擎在调用工具前注入，工具线程内可安全调用
ProgressCallback = Callable[[int, str], None]


class Tool(ABC):
    """工具基类，所有 Tool 插件必须继承此类。"""

    # 子类必须覆盖这三个属性
    name: str = ""
    description: str = ""
    parameters: dict = {}  # JSON Schema 格式

    # ── 声明式配置依赖（OCP：子类声明，引擎自动注入）──
    # 声明该工具需要从 Skill Config 中注入哪些键
    # Agent 引擎在执行工具前自动将匹配的 config 值注入 arguments
    injected_configs: list[str] = []
    # 声明哪些 config 键是必须的（缺失时工具应拒绝执行）
    required_configs: list[str] = []

    # 由异步引擎在执行前注入，工具完成后置 None
    # 工具代码通过 self.report_progress(percent, msg) 调用即可
    _progress_callback: Optional[ProgressCallback] = None

    def report_progress(self, percent: int, message: str = "") -> None:
        """
        向上层报告执行进度（0-100）。

        仅在异步流式引擎 (run_async) 中生效。
        同步调用 (run) 时回调为 None，自动静默跳过。

        用法（在子类 execute 中调用）::

            self.report_progress(30, "正在扫描参考文献...")
            # ... 做一些耗时操作 ...
            self.report_progress(70, "正在检测图注...")
        """
        if self._progress_callback is not None:
            self._progress_callback(
                max(0, min(100, percent)),  # 钳位到 [0, 100]
                message,
            )

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

    def subset(self, names: set[str]) -> "ToolRegistry":
        """创建仅包含指定工具的子注册表（白名单）。用于子 Agent 工具权限隔离。"""
        child = ToolRegistry()
        for name in names:
            tool = self._tools.get(name)
            if tool:
                child._tools[name] = tool
        return child

    def exclude(self, names: set[str]) -> "ToolRegistry":
        """创建排除指定工具的子注册表（黑名单）。用于防止子 Agent 调用 delegate_task 等。"""
        child = ToolRegistry()
        for name, tool in self._tools.items():
            if name not in names:
                child._tools[name] = tool
        return child

    def describe(self) -> str:
        """生成所有工具的文字描述（用于日志/调试）"""
        lines = []
        for tool in self._tools.values():
            lines.append(f"  - {tool.name}: {tool.description}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._tools)
