# -*- coding: utf-8 -*-
"""
Tool: 手写图注转 Word 题注（阶段D）
将手写图注（如"图 1.3 系统模型框图"）转换为带 SEQ 域的 Word 题注，支持自动编号。
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


class FigCaptionTool(Tool):
    name = "convert_handwritten_captions"
    description = (
        "将Word文档中的手写图注转换为Word题注格式。"
        "手写图注如'图 1.3 系统模型框图'会被转为带SEQ域代码的Word题注，"
        "支持自动编号和交叉引用。"
        "【执行顺序】此工具必须在 create_figure_crossrefs 之前执行（通常无需单独调用，"
        "因为 create_figure_crossrefs 会自动包含此步骤）。"
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

        stages = {'A': False, 'B': False, 'C': False, 'D': True, 'E': False}
        self.report_progress(10, "开始转换手写图注...")
        mod.process_document(file_path, modify_in_place=modify_in_place, stages=stages)
        self.report_progress(90, "手写图注转换完成")

        output_path = _get_output_path(file_path, modify_in_place)
        return (
            f"手写图注转换完成。\n"
            f"输出文件路径: {output_path}\n"
            f"如需验证结果，可使用 read_document 工具读取该文件。"
        )
