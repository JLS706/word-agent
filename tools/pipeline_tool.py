# -*- coding: utf-8 -*-
"""
Tool: Multi-Agent 流水线触发器

将 Multi-Agent 流水线（Planner → Executor → Reviewer）包装为一个工具，
让 Agent 在 ReAct 循环中自主决定是否需要启动全流程处理。

用户不再需要手动输入 "pipeline xxx.docx"，
而是由 LLM 根据用户意图自主判断是否调用此工具。
"""

import os
from tools.base import Tool


# 全局引用，在 main.py 注册时注入
_orchestrator = None


class RunPipelineTool(Tool):
    """让 Agent 自主决定是否启动 Multi-Agent 全流程处理"""

    name = "run_pipeline"
    description = (
        "启动 Multi-Agent 全流程处理（Planner→Executor→Reviewer三角色协作）。\n"
        "适用于用户要求'全面处理'、'完整排版'、'帮我把论文排版全部做了'等\n"
        "涉及**多个步骤**的复杂任务。\n"
        "流水线会自动：分析文档 → 制定计划 → 逐步执行 → 验证结果。\n\n"
        "⚠️ 注意：仅当任务涉及多个工具的协作时才使用此工具。\n"
        "如果用户只是要求单一操作（如只检查格式、只处理参考文献），\n"
        "请直接调用对应的工具，不要使用此工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word文档的完整文件路径",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str) -> str:
        if _orchestrator is None:
            return "❌ Multi-Agent 流水线未初始化，无法执行。"

        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return f"文件不存在: {abs_path}"

        try:
            result = _orchestrator.run_pipeline(abs_path)
            return result
        except Exception as e:
            return f"流水线执行出错: {e}"


def set_orchestrator(orchestrator):
    """由 main.py 调用，注入 orchestrator 实例"""
    global _orchestrator
    _orchestrator = orchestrator
