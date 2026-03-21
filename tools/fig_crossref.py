# -*- coding: utf-8 -*-
"""
Tool: 图注交叉引用生成（阶段D+C）
将正文中的图注引用（如"图1.3"）替换为可跳转的 Word 域代码交叉引用。
自动包含手写图注转换（阶段D），确保草稿编号映射不丢失。
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


class FigCrossRefTool(Tool):
    name = "create_figure_crossrefs"
    description = (
        "自动生成Word文档中的图注交叉引用。将正文中的'图X.Y'引用替换为"
        "可点击跳转的Word域代码。此工具会自动将手写图注转为Word题注"
        "（包含草稿编号如图1.A的自动重编号），无需手动调用 convert_handwritten_captions。"
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
                "description": "是否直接覆盖原文件。默认为false。",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, modify_in_place: bool = False) -> str:
        agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, agent_dir)

        import importlib
        spec = importlib.util.spec_from_file_location(
            "word_automation",
            os.path.join(agent_dir, "Word文献自动化精灵.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # D+C 一起执行：D 生成 draft_fig_map，C 使用它来映射草稿编号
        stages = {'A': False, 'B': False, 'C': True, 'D': True, 'E': False}
        mod.process_document(file_path, modify_in_place=modify_in_place, stages=stages)

        output_path = _get_output_path(file_path, modify_in_place)
        return (
            f"图注交叉引用生成完成（含手写图注自动转换）。\n"
            f"输出文件路径: {output_path}\n"
            f"如需验证结果，可使用 read_document 工具读取该文件。"
        )

