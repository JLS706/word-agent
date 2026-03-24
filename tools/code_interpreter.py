# -*- coding: utf-8 -*-
"""
Tool: 安全沙盒 Python 代码解释器
让 Agent 具备"临时写代码并执行"的能力，用于分析、计算等只读任务。

沙盒引擎位于 core/sandbox.py，本文件只是 Tool 包装层。
"""

from tools.base import Tool
from core.sandbox import execute_sandboxed


class CodeInterpreterTool(Tool):
    name = "execute_python"
    description = (
        "安全沙盒 Python 代码解释器。你可以编写并执行任意 Python 代码来完成分析、计算、"
        "文本处理等任务。这是一个只读沙盒，不能修改文件或访问网络。\n"
        "使用场景：\n"
        "- 用正则表达式分析文本格式问题\n"
        "- 统计文档中的数据（词频、字数、参考文献分布等）\n"
        "- 数学计算和数据处理\n"
        "- 对比不同文本段落的风格差异\n"
        "- 任何需要'写个小脚本算一下'的临时需求\n"
        "可用模块: re, math, statistics, collections, json, csv, datetime, "
        "string, textwrap, difflib, itertools, functools, random, hashlib 等。\n"
        "可以用 open(path, 'r') 只读打开文件。禁止写入文件、访问网络或系统操作。\n"
        "代码执行有 5 秒超时限制。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "要执行的 Python 代码。可以使用 print() 输出结果。"
                    "代码在安全沙盒中运行，只允许使用白名单中的模块。"
                ),
            },
        },
        "required": ["code"],
    }

    def execute(self, code: str) -> str:
        if not code or not code.strip():
            return "❌ 代码为空，请提供要执行的 Python 代码。"
        return execute_sandboxed(code)
