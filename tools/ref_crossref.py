# -*- coding: utf-8 -*-
"""
Tool: 文献交叉引用生成（阶段B）
将正文中的 [1][2] 等文献引用替换为可跳转的 Word 域代码交叉引用。
"""

import os
import sys

from tools.base import Tool


def _get_output_path(file_path: str, modify_in_place: bool) -> str:
    abs_path = os.path.abspath(file_path)
    if modify_in_place:
        return abs_path
    base, ext = os.path.splitext(abs_path)
    return f"{base}_processed{ext}"


class RefCrossRefTool(Tool):
    name = "create_reference_crossrefs"
    description = (
        "自动生成Word文档中的参考文献交叉引用。将正文中的[1]、[2]等文献编号替换为"
        "可点击跳转的Word域代码（REF域），支持自动编号更新。"
        "需要文档中已有「参考文献」章节。"
        "【执行顺序】应在 format_references 之后执行。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word文档的完整文件路径",
            },
            "modify_in_place": {
                "type": "boolean",
                "description": "是否直接覆盖原文件。默认为true。与其他工具配合使用时必须为true。",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, modify_in_place: bool = True) -> str:
        agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, agent_dir)

        import importlib
        spec = importlib.util.spec_from_file_location(
            "word_automation",
            os.path.join(agent_dir, "Word文献自动化精灵.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        stages = {'A': False, 'B': True, 'C': False, 'D': False, 'E': False}
        mod.process_document(file_path, modify_in_place=modify_in_place, stages=stages)

        output_path = _get_output_path(file_path, modify_in_place)
        return (
            f"文献交叉引用生成完成。\n"
            f"输出文件路径: {output_path}\n"
            f"如需验证结果，可使用 read_document 工具读取该文件。"
        )
