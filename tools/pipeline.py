# -*- coding: utf-8 -*-
"""
Tool: 文档分析 — 扫描文档现状，供 Agent 自动规划执行计划
"""

import os
import re
import sys

from tools.base import Tool


class AnalyzeDocumentTool(Tool):
    name = "analyze_document"
    description = (
        "分析Word文档的整体状况，检测其中包含哪些可处理的内容。"
        "返回结构化的分析报告，帮助你制定执行计划。"
        "当用户要求'全面处理'或你不确定该执行哪些工具时，应先调用此工具。"
        "这是一个只读工具，不会修改文档。"
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
            report = []
            report.append(f"=== 文档分析报告 ===")
            report.append(f"文件: {os.path.basename(abs_path)}")
            report.append(f"总段落数: {total_paras}")

            # ── 检测1：参考文献 ──
            has_refs = False
            ref_count = 0
            ref_citations_in_body = 0
            for i in range(1, total_paras + 1):
                para = doc.Paragraphs(i)
                text = para.Range.Text.strip()
                if "参考文献" in text and len(text) < 50:
                    has_refs = True
                    continue
                if has_refs and re.match(r'^\[?\d+\]?\s', text):
                    ref_count += 1

            # 检测正文中的文献引用 [N]
            body_text = ""
            for i in range(1, min(total_paras + 1, 300)):
                text = doc.Paragraphs(i).Range.Text.strip()
                if "参考文献" in text and len(text) < 50:
                    break
                body_text += text + "\n"
            ref_citations_in_body = len(re.findall(r'\[\d+\]', body_text))

            if has_refs:
                report.append(f"\n[参考文献] 已检测到参考文献章节")
                report.append(f"  - 参考文献条目数: {ref_count}")
                report.append(f"  - 正文中的引用标记: {ref_citations_in_body} 处")
                report.append(f"  - 建议: 可执行 format_references + create_reference_crossrefs")
            else:
                report.append(f"\n[参考文献] 未检测到参考文献章节")

            # ── 检测2：手写图注 vs Word题注 ──
            handwritten_captions = 0
            word_captions = 0
            fig_refs_in_body = len(re.findall(r'图\s*\d+[\.\-]\d+', body_text))

            for i in range(1, total_paras + 1):
                para = doc.Paragraphs(i)
                text = para.Range.Text.strip()
                # 检测手写图注（居中、短段落、匹配"图X.Y 描述"）
                if re.match(r'^图\s*\d+[\.\-]\d+', text) and len(text) < 80:
                    try:
                        if para.Format.Alignment == 1:  # 居中
                            # 检查是否已有SEQ域
                            has_seq = False
                            for fld in para.Range.Fields:
                                if 'SEQ' in fld.Code.Text.upper():
                                    has_seq = True
                                    break
                            if has_seq:
                                word_captions += 1
                            else:
                                handwritten_captions += 1
                    except Exception:
                        pass

            report.append(f"\n[图注]")
            report.append(f"  - 手写图注: {handwritten_captions} 个")
            report.append(f"  - Word题注(SEQ域): {word_captions} 个")
            report.append(f"  - 正文中的图引用: {fig_refs_in_body} 处")
            if handwritten_captions > 0:
                report.append(f"  - 建议: 先执行 convert_handwritten_captions，再执行 create_figure_crossrefs")
            elif word_captions > 0 and fig_refs_in_body > 0:
                report.append(f"  - 建议: 可直接执行 create_figure_crossrefs")

            # ── 检测3：LaTeX公式 ──
            latex_inline = 0
            latex_block = 0
            for i in range(1, min(total_paras + 1, 500)):
                text = doc.Paragraphs(i).Range.Text
                latex_inline += len(re.findall(r'(?<!\$)\$(?!\$).+?\$(?!\$)', text))
                latex_block += len(re.findall(r'\$\$.+?\$\$', text))

            report.append(f"\n[LaTeX公式]")
            if latex_inline + latex_block > 0:
                report.append(f"  - 行内公式($...$): {latex_inline} 个")
                report.append(f"  - 块公式($$...$$): {latex_block} 个")
                report.append(f"  - 建议: 可执行 convert_latex_to_mathtype（需MathType）")
            else:
                report.append(f"  - 未检测到LaTeX公式")

            # ── 检测4：缩写词 ──
            acronyms = set(re.findall(r'\b[A-Z]{2,6}\b', body_text))
            common_words = {'IEEE', 'ACM', 'DOI', 'HTTP', 'URL', 'PDF', 'USB', 'GPS'}
            acronyms -= common_words
            report.append(f"\n[缩写词]")
            report.append(f"  - 检测到 {len(acronyms)} 个疑似专业缩写")
            if acronyms:
                sample = list(acronyms)[:8]
                report.append(f"  - 示例: {', '.join(sample)}")
                report.append(f"  - 建议: 可执行 check_acronym_definitions")

            # ── 执行计划建议 ──
            plan = []
            if handwritten_captions > 0:
                plan.append("1. convert_handwritten_captions (手写图注转题注)")
            if (word_captions > 0 or handwritten_captions > 0) and fig_refs_in_body > 0:
                plan.append(f"{len(plan)+1}. create_figure_crossrefs (图注交叉引用)")
            if has_refs and ref_count > 0:
                plan.append(f"{len(plan)+1}. format_references (参考文献格式化)")
            if has_refs and ref_citations_in_body > 0:
                plan.append(f"{len(plan)+1}. create_reference_crossrefs (文献交叉引用)")
            if acronyms:
                plan.append(f"{len(plan)+1}. check_acronym_definitions (缩写词检测)")
            if latex_inline + latex_block > 0:
                plan.append(f"{len(plan)+1}. convert_latex_to_mathtype (LaTeX公式转换)")

            if plan:
                report.append(f"\n=== 推荐执行计划 ===")
                for step in plan:
                    report.append(f"  {step}")
                report.append(f"\n注意: 请严格按以上顺序执行，尤其是图注转换必须在图注交叉引用之前。")

            if opened_by_us:
                doc.Close(SaveChanges=0)

            return "\n".join(report)

        except Exception as e:
            return f"分析文档时出错: {e}"
