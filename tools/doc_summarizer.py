# -*- coding: utf-8 -*-
"""
DocMaster Agent - 文档摘要工具（Map-Reduce）

解决的问题：
  用户要求"总结全文"时，read_document 返回的内容太长，
  LLM 上下文放不下，导致只总结了前面一小段。

解决方案（Map-Reduce 摘要）：
  1. Map：将文档按章节/段落分块，每块独立让 LLM 生成 mini 摘要
  2. Reduce：将所有 mini 摘要合成一份完整概要

优点：
  - 每次 LLM 只处理 ~1000 字，不会 Lost in the middle
  - 最终摘要覆盖全文，不会只总结开头
  - 不需要向量检索，适合"整体理解"场景
"""

import os
import re
from tools.base import Tool as BaseTool
from core.logger import logger


def _read_docx_text(file_path: str) -> str:
    """从 Word 文档中提取纯文本（复用 rag.py 的逻辑）"""
    try:
        from docx import Document
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except ImportError:
        try:
            import win32com.client
            word = win32com.client.GetObject(Class="Word.Application")
            doc = word.Documents.Open(os.path.abspath(file_path))
            text = doc.Content.Text
            doc.Close(False)
            return text
        except Exception as e:
            raise RuntimeError(f"无法读取文档: {e}")


def _split_by_sections(text: str, max_chunk: int = 1500) -> list[str]:
    """
    按章节结构分块。

    策略（递归分割）：
      1. 先按标题行分（数字编号开头的行，如 "1. 引言"、"2.1 方法"）
      2. 如果某节太长，按段落分
      3. 如果段落还是太长，按固定大小切割

    全程不需要 LLM，利用文档自身的结构信号。
    """
    # 按标题行分割（匹配 "1. xxx"、"2.1 xxx"、"第x章" 等模式）
    section_pattern = r'\n(?=\d+[\.\s]|第[一二三四五六七八九十\d]+[章节]|[A-Z]+[\.\s])'
    sections = re.split(section_pattern, text)
    sections = [s.strip() for s in sections if s.strip()]

    # 如果没有分出章节（无结构的文档），按段落分
    if len(sections) <= 1:
        sections = re.split(r'\n\s*\n', text)
        sections = [s.strip() for s in sections if s.strip()]

    # 合并过短的段落、切割过长的段落
    chunks = []
    current = ""
    for sec in sections:
        if len(current) + len(sec) <= max_chunk:
            current = (current + "\n\n" + sec).strip()
        else:
            if current:
                chunks.append(current)
            # 段落本身超长 → 按固定大小切割
            if len(sec) > max_chunk:
                for i in range(0, len(sec), max_chunk - 100):
                    sub = sec[i:i + max_chunk]
                    if sub.strip():
                        chunks.append(sub.strip())
            else:
                current = sec
                continue
            current = ""

    if current.strip():
        chunks.append(current.strip())

    return chunks if chunks else [text[:max_chunk]]


class SummarizeDocumentTool(BaseTool):
    """
    文档全文摘要工具（Map-Reduce）。

    适用场景：用户要求"总结论文"、"概括全文"、"文档大意"。
    不适用：查找特定内容（应该用 search_document）。
    """

    name = "summarize_document"
    description = (
        "对 Word 文档进行全文摘要。适用于用户要求'总结论文'、'概括全文'等场景。"
        "采用 Map-Reduce 策略：先分段摘要，再合成总结，确保覆盖全文。"
        "如果用户只需要查找特定内容，应使用 search_document 而非此工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word 文档路径（.docx）",
            },
            "detail_level": {
                "type": "string",
                "description": "摘要详细程度: 'brief'(3-5句) 或 'detailed'(一页), 默认 'brief'",
                "enum": ["brief", "detailed"],
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, detail_level: str = "brief", **kwargs) -> str:
        if not os.path.exists(file_path):
            return f"文件不存在: {file_path}"

        # 读取全文
        try:
            text = _read_docx_text(file_path)
        except Exception as e:
            return f"读取文档失败: {e}"

        if not text.strip():
            return "文档内容为空"

        self.report_progress(10, "文档读取完成")

        # 如果文档够短（<2000字），直接返回全文让 LLM 处理
        if len(text) < 2000:
            return f"[文档较短，直接返回全文]\n\n{text}"

        # ── Map 阶段：分块 + 逐块摘要 ──
        chunks = _split_by_sections(text, max_chunk=1500)
        logger.info("📄 文档分为 %d 个段落块，开始 Map-Reduce 摘要...", len(chunks))

        # 获取 LLM
        import toml
        from core.llm import LLM
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "config.toml"
        )
        config = toml.load(config_path)
        llm_config = config.get("llm", {})
        llm = LLM(**llm_config)

        from core.schema import Message, Role

        mini_summaries = []
        for i, chunk in enumerate(chunks):
            pct = 15 + int(60 * i / max(len(chunks), 1))
            self.report_progress(pct, f"Map {i+1}/{len(chunks)}")
            logger.debug("  Map %d/%d (%d 字)...", i + 1, len(chunks), len(chunk))
            messages = [
                Message(role=Role.SYSTEM, content=(
                    "你是一个学术文档摘要助手。"
                    "请用 2-3 句话概括以下段落的核心内容。"
                    "保留关键术语、数据和结论。不要添加原文没有的内容。"
                )),
                Message(role=Role.USER, content=f"请概括以下内容：\n\n{chunk}"),
            ]
            try:
                response = llm.chat(messages)
                summary = response.content or ""
                mini_summaries.append(f"[段落 {i+1}] {summary}")
            except Exception as e:
                mini_summaries.append(f"[段落 {i+1}] (摘要生成失败: {e})")

        # ── Reduce 阶段：合成总结 ──
        self.report_progress(80, "Reduce 阶段：合成摘要...")
        logger.info("📝 Reduce 阶段：合成 %d 个段落摘要...", len(mini_summaries))

        combined = "\n\n".join(mini_summaries)

        if detail_level == "brief":
            reduce_prompt = (
                "以下是一篇学术论文各段落的摘要。"
                "请将它们合成为一份 3-5 句话的简洁概要，涵盖：研究背景、方法、关键结果和结论。"
            )
        else:
            reduce_prompt = (
                "以下是一篇学术论文各段落的摘要。"
                "请将它们合成为一份详细的论文概要（约 300-500 字），"
                "涵盖：研究背景与动机、方法论、实验设计、关键结果、结论与未来工作。"
            )

        messages = [
            Message(role=Role.SYSTEM, content=reduce_prompt),
            Message(role=Role.USER, content=f"各段落摘要：\n\n{combined}"),
        ]

        try:
            response = llm.chat(messages)
            final_summary = response.content or "(合成失败)"
        except Exception as e:
            final_summary = f"(合成阶段失败: {e})\n\n各段落摘要：\n{combined}"

        stats = (
            f"\n\n---\n"
            f"📊 统计: 原文 {len(text)} 字 → {len(chunks)} 个段落 → "
            f"最终摘要 {len(final_summary)} 字 "
            f"(压缩率 {round(len(final_summary)/len(text)*100, 1)}%)"
        )

        return final_summary + stats
