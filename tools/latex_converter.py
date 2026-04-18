# -*- coding: utf-8 -*-
"""
Tool: LaTeX → MathType 批量转换
将Word文档中的 $...$ 和 $$...$$ LaTeX公式转换为 MathType OLE 对象。
"""

import os
import sys

from tools.base import Tool


class LatexConverterTool(Tool):
    name = "convert_latex_to_mathtype"
    description = (
        "将Word文档中的LaTeX公式（$...$行内公式和$$...$$块公式）批量转换为MathType公式对象。"
        "需要电脑上已安装MathType软件。转换过程会逐个处理文档中的公式，"
        "通过自动化操控MathType完成LaTeX到OLE对象的转换。"
        "⚠️ 此工具执行时间较长，且需要MathType窗口处于可用状态。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word文档的完整文件路径",
            },
            "overwrite": {
                "type": "boolean",
                "description": "是否覆盖原文件（true=覆盖并自动备份，false=另存为_converted.docx）。默认false。",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, overwrite: bool = False) -> str:
        agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        latex_script = os.path.join(agent_dir, "latex.py")

        if not os.path.exists(latex_script):
            return "❌ 未找到 latex.py 脚本文件"

        # LaTeX 转换脚本使用 sys.argv 接收参数
        # 为了避免修改原脚本，我们操作 sys.argv 然后调用 main()
        import importlib
        spec = importlib.util.spec_from_file_location("latex_converter", latex_script)
        mod = importlib.util.module_from_spec(spec)

        original_argv = sys.argv
        try:
            sys.argv = ["latex.py", file_path]
            if overwrite:
                sys.argv.append("--overwrite")
            else:
                sys.argv.append("--safe")
            self.report_progress(5, "开始 LaTeX→MathType 转换（耗时较长）...")
            spec.loader.exec_module(mod)
            mod.main()
            self.report_progress(95, "LaTeX 转换完成")
        finally:
            sys.argv = original_argv

        self.report_progress(100, "完成")
        return f"✅ LaTeX→MathType 转换完成。文件: {os.path.basename(file_path)}"
