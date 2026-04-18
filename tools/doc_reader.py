# -*- coding: utf-8 -*-
"""
Tool: Word 文档内容读取
提取 Word 文档的文本内容，供 LLM 进行智能分析、审查和问答。
这是一个只读工具，不会修改文档。
"""

import os
import sys

from tools.base import Tool


class DocReaderTool(Tool):
    name = "read_document"
    description = (
        "读取Word文档的文本内容，用于分析和审查。可以读取全文或指定部分。"
        "读取后你可以基于内容进行各种智能分析，例如：\n"
        "- 检查参考文献格式是否规范\n"
        "- 审查交叉引用是否正确\n"
        "- 检查论文结构是否完整\n"
        "- 统计字数、段落数等信息\n"
        "- 检查中英文混排格式\n"
        "- 回答用户关于文档内容的任何问题\n"
        "这是一个只读工具，不会修改文档。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word文档的完整文件路径",
            },
            "section": {
                "type": "string",
                "description": (
                    "要读取的部分。可选值：\n"
                    "- 'all': 读取全文（默认，但会截断过长内容）\n"
                    "- 'references': 只读取参考文献部分\n"
                    "- 'structure': 只读取章节标题结构\n"
                    "- 'first_n': 读取前N段（N通过max_paragraphs指定）"
                ),
            },
            "max_paragraphs": {
                "type": "integer",
                "description": "最多读取的段落数（默认100，防止内容过长超出LLM上下文限制）",
            },
        },
        "required": ["file_path"],
    }

    def execute(
        self,
        file_path: str,
        section: str = "all",
        max_paragraphs: int = 100,
    ) -> str:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return f"文件不存在: {abs_path}"

        try:
            import win32com.client

            word = win32com.client.Dispatch("Word.Application")
            word.Visible = True

            # 检查文档是否已打开
            doc = None
            try:
                for d in word.Documents:
                    if os.path.abspath(d.FullName).lower() == abs_path.lower():
                        doc = d
                        break
            except Exception:
                pass

            if doc is None:
                doc = word.Documents.Open(abs_path)
                opened_by_us = True
            else:
                opened_by_us = False

            total_paras = doc.Paragraphs.Count
            self.report_progress(20, "文档已打开")
            result_lines = []
            result_lines.append(f"[文档信息] 文件: {os.path.basename(abs_path)}")
            result_lines.append(f"[文档信息] 总段落数: {total_paras}")

            if section == "structure":
                # 只提取标题（Heading样式的段落）
                result_lines.append("\n--- 文档结构 ---")
                for i in range(1, total_paras + 1):
                    para = doc.Paragraphs(i)
                    try:
                        style_name = str(para.Style.NameLocal)
                        if "标题" in style_name or "Heading" in style_name:
                            level = ""
                            for ch in style_name:
                                if ch.isdigit():
                                    level += ch
                            indent = "  " * (int(level) - 1) if level else ""
                            text = para.Range.Text.strip()
                            if text:
                                result_lines.append(f"{indent}{text}")
                    except Exception:
                        pass

            elif section == "references":
                # 只提取参考文献部分
                result_lines.append("\n--- 参考文献 ---")
                in_refs = False
                count = 0
                for i in range(1, total_paras + 1):
                    para = doc.Paragraphs(i)
                    text = para.Range.Text.strip()
                    if not in_refs:
                        if "参考文献" in text and len(text) < 50:
                            in_refs = True
                        continue
                    if not text:
                        continue
                    # 遇到致谢/附录等则停止
                    if len(text) < 50:
                        for kw in ["致谢", "附录", "基金项目", "作者简介",
                                    "Acknowledgment", "Appendix"]:
                            if kw in text:
                                in_refs = False
                                break
                    if not in_refs:
                        break
                    count += 1
                    result_lines.append(f"[{count}] {text}")
                    if count >= max_paragraphs:
                        result_lines.append(f"... (截断，共有更多条目)")
                        break

            else:
                # 读取全文或前N段
                result_lines.append(f"\n--- 文档内容（前{max_paragraphs}段）---")
                read_count = 0
                for i in range(1, min(total_paras + 1, max_paragraphs + 1)):
                    para = doc.Paragraphs(i)
                    text = para.Range.Text.strip()
                    if text:
                        result_lines.append(text)
                        read_count += 1
                if total_paras > max_paragraphs:
                    result_lines.append(
                        f"\n... (已截断，文档共{total_paras}段，"
                        f"仅显示前{max_paragraphs}段)"
                    )

            self.report_progress(90, "内容提取完成")
            # 不关闭我们没打开的文档
            if opened_by_us:
                doc.Close(SaveChanges=0)

            self.report_progress(100, "读取完成")
            return "\n".join(result_lines)

        except Exception as e:
            return f"读取文档时出错: {e}"
