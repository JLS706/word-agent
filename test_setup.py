# -*- coding: utf-8 -*-
"""验证脚本：测试所有模块导入和工具注册"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 测试1：核心模块导入
from core.schema import Message, AgentState, ToolCall, ToolResult, Role
from core.prompt import build_system_prompt
from tools.base import Tool, ToolRegistry
print("[OK] Core modules imported")

# 测试2：工具导入与注册
from tools.ref_formatter import RefFormatterTool
from tools.ref_crossref import RefCrossRefTool
from tools.fig_crossref import FigCrossRefTool
from tools.fig_caption import FigCaptionTool
from tools.acronym_checker import AcronymCheckerTool
from tools.latex_converter import LatexConverterTool

registry = ToolRegistry()
tools = [
    RefFormatterTool(),
    RefCrossRefTool(),
    FigCrossRefTool(),
    FigCaptionTool(),
    AcronymCheckerTool(),
    LatexConverterTool(),
]
for t in tools:
    registry.register(t)
print(f"[OK] Registered {len(registry)} tools")

# 测试3：OpenAI tools 格式转换
openai_tools = registry.to_openai_tools()
for i, t in enumerate(openai_tools):
    name = t["function"]["name"]
    params = list(t["function"]["parameters"].get("properties", {}).keys())
    print(f"  [{i+1}] {name} (params: {params})")
print(f"[OK] OpenAI format conversion OK")

# 测试4：Message 序列化
msg = Message(role=Role.USER, content="test message")
d = msg.to_dict()
assert d["role"] == "user"
assert d["content"] == "test message"
print("[OK] Message serialization OK")

# 测试5：System Prompt 生成
desc = registry.describe()
prompt = build_system_prompt(desc)
assert "DocMaster" in prompt
assert "format_references" in prompt
print("[OK] System prompt generation OK")

print("\n=== ALL TESTS PASSED ===")
