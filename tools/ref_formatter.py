# -*- coding: utf-8 -*-
"""
Tool: 参考文献格式修复（阶段A）
统一参考文献的字体字号、Sentence Case 处理、期刊名斜体。
"""

import os
import sys

from tools.base import Tool


def _get_output_path(file_path: str, modify_in_place: bool) -> str:
    """计算输出文件路径（与原脚本逻辑一致）"""
    abs_path = os.path.abspath(file_path)
    if modify_in_place:
        return abs_path
    base, ext = os.path.splitext(abs_path)
    return f"{base}_processed{ext}"


class RefFormatterTool(Tool):
    name = "format_references"
    description = (
        "格式化Word文档中的参考文献列表。包括：统一字体字号（宋体+Times New Roman，五号）、"
        "英文标题转Sentence Case、期刊名/会议名自动斜体。"
        "需要提供Word文档的完整文件路径。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word文档的完整文件路径，如 C:\\Users\\xxx\\论文.docx",
            },
            "modify_in_place": {
                "type": "boolean",
                "description": "是否直接覆盖原文件。默认为false（另存副本）。",
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

        stages = {'A': True, 'B': False, 'C': False, 'D': False, 'E': False}
        mod.process_document(file_path, modify_in_place=modify_in_place, stages=stages)

        output_path = _get_output_path(file_path, modify_in_place)
        return (
            f"参考文献格式修复完成。\n"
            f"输出文件路径: {output_path}\n"
            f"如需验证结果，可使用 read_document 工具读取该文件。"
        )
