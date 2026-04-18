# -*- coding: utf-8 -*-
"""
Tool: 参考文献格式修复（阶段A）

设计哲学（Tool-Skill 分离）：
  - 工具 = 纯能力引擎：知道 HOW（如何扫描和修复参考文献格式）
  - Skill = 领域知识：提供具体的字体字号参数（通过 ref_format_config）
  - 当 Skill 未提供参数时，工具仍可执行（委托给底层脚本的默认逻辑）
    但会提示建议加载 Skill 以确保格式符合特定规范
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
        "格式化Word文档中的参考文献列表。包括：统一字体字号、"
        "英文标题转Sentence Case、期刊名/会议名自动斜体。\n"
        "具体的字体字号标准由 Skill config 中的 ref_format_config 提供。\n"
        "需要提供Word文档的完整文件路径。\n"
        "【执行顺序】此工具应在 create_reference_crossrefs 之前执行。"
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
                "description": "是否直接覆盖原文件。默认为true。与其他工具配合使用时必须为true。",
            },
        },
        "required": ["file_path"],
    }

    def execute(
        self,
        file_path: str,
        modify_in_place: bool = True,
        ref_format_config: dict = None,
    ) -> str:
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

        # 构建结果报告，包含 Skill 注入状态
        config_info = ""
        if ref_format_config:
            font_cn = ref_format_config.get("font_cn", "未指定")
            font_en = ref_format_config.get("font_en", "未指定")
            font_size = ref_format_config.get("font_size", "未指定")
            config_info = (
                f"\n使用的格式规范（由 Skill 注入）："
                f"\n  - 中文字体: {font_cn}"
                f"\n  - 西文字体: {font_en}"
                f"\n  - 字号: {font_size}pt"
            )
        else:
            config_info = (
                "\n⚠️ 未检测到 Skill 提供的 ref_format_config，"
                "使用了底层脚本的内置默认值。"
                "\n💡 建议加载排版规范 Skill 以确保格式符合特定学校/期刊要求。"
            )

        return (
            f"参考文献格式修复完成。\n"
            f"输出文件路径: {output_path}"
            f"{config_info}\n"
            f"如需验证结果，可使用 read_document 工具读取该文件。"
        )
