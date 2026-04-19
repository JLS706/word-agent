# -*- coding: utf-8 -*-
"""
Tool: 缩写定义检测（阶段E）
检测专业缩写词在第一次出现时是否写了全称定义。
"""

import os
import sys
import io

from tools.base import Tool


class AcronymCheckerTool(Tool):
    name = "check_acronym_definitions"
    description = (
        "检测Word文档中的专业缩写词（如MIMO、OFDM等）在首次出现时是否给出了全称定义。"
        "这是一个只读检测工具，不会修改文档内容。结果会列出所有未定义的缩写及其所在段落。"
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
        from core.com_watchdog import COMSafeLock

        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return f"❌ 文件不存在: {abs_path}"

        agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, agent_dir)

        import importlib
        spec = importlib.util.spec_from_file_location(
            "word_automation",
            os.path.join(agent_dir, "Word文献自动化精灵.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 捕获 print 输出作为结果
        captured = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            stages = {'A': False, 'B': False, 'C': False, 'D': False, 'E': True}
            self.report_progress(5, "开始检测缩写定义...")

            # 只读模式：COMSafeLock 退出时不保存文档
            with COMSafeLock(abs_path, read_only=True) as (word_app, doc_obj):
                mod.process_document(
                    abs_path,
                    modify_in_place=False,
                    stages=stages,
                    word=word_app,
                    doc=doc_obj,
                    progress_callback=lambda pct, msg: self.report_progress(pct, msg),
                )

            self.report_progress(95, "缩写检测完成")
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        if not output.strip():
            output = "✅ 缩写定义检测完成，未发现问题。"
        return output
