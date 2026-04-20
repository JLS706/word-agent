# -*- coding: utf-8 -*-
"""
Tool: 引用溯源审计（Citation Verification）

核心功能：
  1. 提取综述正文中所有带引用标记的主张句（如"MIMO 技术可提升30%吞吐量[1]"）
  2. 对每条主张，在对应原文文献的向量库中做 Top-K 检索，召回最相关段落
  3. 将"主张 + 原文段落"交给 LLM 做忠实度判决（严苛审稿人视角）
  4. 输出带溯源对比的审计报告

依赖：
  - tools/rag.py 中的 IndexDocumentTool / _get_embed_client / _read_docx_text
  - core/embeddings.py 中的 VectorStore
  - core/llm.py 中的 LLM（用于判决步骤）
  - core/semantic_chunker.py（语义切块）

挂载位置：
  Reviewer Agent 的高阶 Skill；也可作为 Coordinator 直接调用的独立工具。
"""

import os
import re
from typing import Optional

from core.logger import logger
from tools.base import Tool

# ────────────────────────────────────────────────
# 引用提取正则
# ────────────────────────────────────────────────
# 匹配 [1], [1,2], [1-3], [1, 3-5], 【1】 等
_CITE_PATTERN = re.compile(
    r'[\[【]\s*(\d+(?:\s*[,，\-~～\u2013\u2014]\s*\d+)*)\s*[\]】]'
)

# 忠实度判决 Prompt 模板
_VERIFICATION_PROMPT = """\
你是一位严苛的学术审稿人（Reviewer 2）。你的任务是判断作者的主张是否忠实于原文。

## 作者的主张（来自综述正文）

> {claim}

## 原文段落（来自被引用的文献 [{ref_key}]）

{evidence}

## 评判标准

请逐条评估：
1. **事实准确性**：主张中的数据、结论是否与原文一致？
2. **过度引申**：作者是否把原文的有限结论扩大为普遍性结论？
3. **语义偏移**：原文的立场/限定条件是否被忽略？
4. **遗漏关键上下文**：原文是否有重要前提/条件被省略？

## 输出格式（严格 JSON）

```json
{{
  "verdict": "FAITHFUL | MINOR_ISSUE | MAJOR_ISSUE | UNSUPPORTED",
  "confidence": 0.0-1.0,
  "analysis": "简要分析（2-3句话）",
  "issues": ["问题1", "问题2"]
}}
```

只输出 JSON，不要输出其他内容。
"""


class VerifyCitationsTool(Tool):
    """
    引用溯源审计工具。

    读取用户的综述/论文正文 → 提取带引用标记的主张句 →
    在原文文献向量库中检索证据 → LLM 忠实度判决 → 输出审计报告。
    """

    name = "verify_citations"
    description = (
        "对综述/论文中的引用进行溯源审计。"
        "提取带引用标记的句子（如'MIMO技术提升30%吞吐量[1]'），"
        "在对应原文文献中检索相关段落，由 LLM 判断引用是否忠实于原文。"
        "需要先用 index_document 为每篇被引文献建立索引。\n"
        "输出：带溯源对比的审计报告，标注 FAITHFUL / MINOR_ISSUE / MAJOR_ISSUE / UNSUPPORTED。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "thesis_path": {
                "type": "string",
                "description": "用户综述/论文的 Word 文档路径（要审计的文档）",
            },
            "ref_sources": {
                "type": "object",
                "description": (
                    "引用编号 → 原文文献路径的映射。"
                    '例如: {"1": "C:/papers/mimo.docx", "2": "C:/papers/ofdm.pdf"}'
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "每条主张检索的原文段落数量，默认 3",
            },
            "max_claims": {
                "type": "integer",
                "description": "最大审计主张数（控制 API 调用量），默认 20",
            },
        },
        "required": ["thesis_path", "ref_sources"],
    }

    def __init__(self, llm=None):
        """
        Args:
            llm: LLM 实例，用于忠实度判决。
                 由 main.py 在注册时传入（复用 Coordinator 的 LLM 连接）。
        """
        self._llm = llm

    def execute(
        self,
        thesis_path: str,
        ref_sources: dict,
        top_k: int = 3,
        max_claims: int = 20,
        **kwargs,
    ) -> str:
        if not os.path.exists(thesis_path):
            return f"❌ 综述文档不存在: {thesis_path}"

        if not ref_sources:
            return "❌ 未提供任何原文文献路径（ref_sources 为空）"

        if self._llm is None:
            return "❌ LLM 未注入，无法执行忠实度判决"

        self.report_progress(2, "开始引用溯源审计...")

        # ── 1. 读取综述正文 ──
        self.report_progress(5, "读取综述文档...")
        try:
            thesis_text = self._read_text(thesis_path)
        except Exception as e:
            return f"❌ 读取综述文档失败: {e}"

        if not thesis_text.strip():
            return "❌ 综述文档内容为空"

        # ── 2. 提取带引用标记的主张句 ──
        self.report_progress(10, "提取带引用标记的主张句...")
        claims = self._extract_claims(thesis_text)
        if not claims:
            return "ℹ️ 未在综述中发现带引用标记的句子（如 [1], [2] 等）。"

        logger.info("[CitationVerifier] 提取到 %d 条带引用主张", len(claims))
        claims = claims[:max_claims]  # 截断

        # ── 3. 为每篇文献建立/加载向量索引 ──
        self.report_progress(15, f"索引 {len(ref_sources)} 篇原文文献...")
        ref_stores = {}
        for ref_key, ref_path in ref_sources.items():
            ref_key = str(ref_key).strip()
            if not os.path.exists(ref_path):
                logger.warning("[CitationVerifier] 文献 [%s] 路径不存在: %s", ref_key, ref_path)
                continue
            try:
                store = self._index_reference(ref_key, ref_path)
                ref_stores[ref_key] = store
                self.report_progress(
                    15 + int(15 * len(ref_stores) / max(len(ref_sources), 1)),
                    f"已索引文献 [{ref_key}]",
                )
            except Exception as e:
                logger.warning("[CitationVerifier] 索引文献 [%s] 失败: %s", ref_key, e)

        if not ref_stores:
            return "❌ 所有原文文献索引均失败，无法执行溯源审计。"

        # ── 4. 逐条主张：检索 + 判决 ──
        self.report_progress(35, f"开始对 {len(claims)} 条主张执行溯源审计...")
        results = []

        for i, claim_info in enumerate(claims):
            claim_text = claim_info["sentence"]
            ref_keys = claim_info["ref_keys"]
            pct = 35 + int(55 * i / max(len(claims), 1))

            self.report_progress(pct, f"审计第 {i+1}/{len(claims)} 条主张...")

            # 对每个引用编号检索 + 判决
            for rk in ref_keys:
                if rk not in ref_stores:
                    results.append({
                        "claim": claim_text,
                        "ref_key": rk,
                        "verdict": "SKIPPED",
                        "reason": f"文献 [{rk}] 未索引",
                        "evidence": [],
                        "analysis": "",
                        "issues": [],
                    })
                    continue

                # 检索
                store = ref_stores[rk]
                evidence = self._search_evidence(store, claim_text, top_k)

                if not evidence:
                    results.append({
                        "claim": claim_text,
                        "ref_key": rk,
                        "verdict": "UNSUPPORTED",
                        "reason": "在原文中未找到相关段落",
                        "evidence": [],
                        "analysis": "向量检索未返回任何相关段落，该引用可能不存在于提供的文献中。",
                        "issues": ["原文中未找到支撑该主张的段落"],
                    })
                    continue

                # LLM 判决
                verdict_result = self._verify_claim(claim_text, rk, evidence)
                results.append({
                    "claim": claim_text,
                    "ref_key": rk,
                    "evidence": [e["chunk"] for e in evidence],
                    **verdict_result,
                })

        # ── 5. 编译审计报告 ──
        self.report_progress(92, "生成审计报告...")
        report = self._compile_report(results, len(claims), len(ref_stores))
        self.report_progress(98, "审计完成")

        return report

    # ================================================================
    # 内部方法
    # ================================================================

    @staticmethod
    def _read_text(file_path: str) -> str:
        """读取文档纯文本（复用 rag.py 的逻辑，支持 PDF）"""
        from tools.rag import _read_text
        return _read_text(file_path)

    @staticmethod
    def _extract_claims(text: str) -> list[dict]:
        """
        从文本中提取带引用标记的句子。

        返回:
            [{"sentence": "MIMO 技术可提升30%吞吐量[1]。", "ref_keys": ["1"]}, ...]
        """
        claims = []
        # 按句号/感叹号/问号分句（保护小数点和缩写）
        sentences = re.split(r'(?<=[。！？.!?])\s*', text)

        for sent in sentences:
            sent = sent.strip()
            if not sent or len(sent) < 10:
                continue

            matches = list(_CITE_PATTERN.finditer(sent))
            if not matches:
                continue

            # 解析引用编号
            ref_keys = set()
            for m in matches:
                inner = m.group(1)
                # 处理逗号分隔: [1,2,3]
                for part in re.split(r'[,，]', inner):
                    part = part.strip()
                    # 处理范围: 1-3
                    range_match = re.match(r'(\d+)\s*[-~～\u2013\u2014]\s*(\d+)', part)
                    if range_match:
                        start, end = int(range_match.group(1)), int(range_match.group(2))
                        for n in range(start, end + 1):
                            ref_keys.add(str(n))
                    elif part.isdigit():
                        ref_keys.add(part)

            if ref_keys:
                claims.append({
                    "sentence": sent,
                    "ref_keys": sorted(ref_keys, key=int),
                })

        return claims

    @staticmethod
    def _index_reference(ref_key: str, file_path: str):
        """为单篇文献建立向量索引（复用 rag.py 多文献库，支持 PDF）"""
        from tools.rag import (
            _index_one_literature, _literature_stores,
        )

        # 如果已经在多文献库中，直接复用
        if ref_key in _literature_stores:
            logger.info("[CitationVerifier] 文献 [%s] 已在文献库中复用", ref_key)
            return _literature_stores[ref_key]

        _index_one_literature(ref_key, file_path)
        return _literature_stores[ref_key]

    @staticmethod
    def _search_evidence(store, query: str, top_k: int = 3) -> list[dict]:
        """在文献向量库中检索最相关段落"""
        from tools.rag import _get_embed_client
        embed_client = _get_embed_client()
        query_embedding = embed_client.embed(query)
        return store.search(query_embedding, top_k=top_k)

    def _verify_claim(
        self, claim: str, ref_key: str, evidence: list[dict]
    ) -> dict:
        """调用 LLM 对单条主张做忠实度判决"""
        import json as _json
        from core.schema import Message, Role

        # 格式化证据段落
        evidence_text = ""
        for i, ev in enumerate(evidence, 1):
            score_pct = round(ev.get("score", 0) * 100, 1)
            evidence_text += f"### 段落 {i}（相关度: {score_pct}%）\n{ev['chunk']}\n\n"

        prompt = _VERIFICATION_PROMPT.format(
            claim=claim,
            ref_key=ref_key,
            evidence=evidence_text,
        )

        try:
            response = self._llm.chat([
                Message(role=Role.SYSTEM, content="你是一位严格的学术审稿人。只输出 JSON。"),
                Message(role=Role.USER, content=prompt),
            ])

            # 从 LLM 响应中提取 JSON
            resp_text = response.content or ""
            json_match = re.search(r'\{[\s\S]*\}', resp_text)
            if json_match:
                parsed = _json.loads(json_match.group())
                return {
                    "verdict": parsed.get("verdict", "UNKNOWN"),
                    "confidence": parsed.get("confidence", 0.0),
                    "analysis": parsed.get("analysis", ""),
                    "issues": parsed.get("issues", []),
                }
        except Exception as e:
            logger.warning("[CitationVerifier] LLM 判决失败: %s", e)

        return {
            "verdict": "ERROR",
            "confidence": 0.0,
            "analysis": "LLM 判决调用失败",
            "issues": [],
        }

    @staticmethod
    def _compile_report(results: list[dict], total_claims: int, total_refs: int) -> str:
        """编译最终审计报告"""
        # 统计
        verdict_counts = {}
        for r in results:
            v = r.get("verdict", "UNKNOWN")
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

        faithful = verdict_counts.get("FAITHFUL", 0)
        minor = verdict_counts.get("MINOR_ISSUE", 0)
        major = verdict_counts.get("MAJOR_ISSUE", 0)
        unsupported = verdict_counts.get("UNSUPPORTED", 0)
        skipped = verdict_counts.get("SKIPPED", 0)
        errors = verdict_counts.get("ERROR", 0)

        total_checked = len(results) - skipped - errors
        pass_rate = (faithful / max(total_checked, 1)) * 100

        lines = [
            "# 📋 引用溯源审计报告",
            "",
            f"**审计范围**: {total_claims} 条带引用主张 × {total_refs} 篇原文文献",
            f"**检查结果**: {len(results)} 条引用-文献对",
            "",
            "## 📊 总体统计",
            "",
            f"| 判定 | 数量 | 含义 |",
            f"|------|------|------|",
            f"| ✅ FAITHFUL | {faithful} | 忠实于原文 |",
            f"| ⚠️ MINOR_ISSUE | {minor} | 存在轻微偏差 |",
            f"| ❌ MAJOR_ISSUE | {major} | 存在严重曲解 |",
            f"| 🚫 UNSUPPORTED | {unsupported} | 原文中未找到支撑 |",
            f"| ⏭️ SKIPPED | {skipped} | 文献未索引，跳过 |",
            f"| 💥 ERROR | {errors} | 判决失败 |",
            "",
            f"**忠实率**: {pass_rate:.1f}%",
            "",
        ]

        # 问题清单（只列出有问题的）
        problem_results = [
            r for r in results
            if r.get("verdict") in ("MINOR_ISSUE", "MAJOR_ISSUE", "UNSUPPORTED")
        ]

        if problem_results:
            lines.append("## 🔍 问题清单")
            lines.append("")

            for i, r in enumerate(problem_results, 1):
                verdict_icon = {
                    "MINOR_ISSUE": "⚠️",
                    "MAJOR_ISSUE": "❌",
                    "UNSUPPORTED": "🚫",
                }.get(r["verdict"], "❓")

                lines.append(f"### {verdict_icon} 问题 {i}（文献 [{r['ref_key']}]）")
                lines.append("")
                lines.append(f"**主张**: {r['claim']}")
                lines.append("")
                lines.append(f"**判定**: {r['verdict']}（置信度: {r.get('confidence', 0):.0%}）")
                lines.append("")
                lines.append(f"**分析**: {r.get('analysis', '无')}")
                lines.append("")

                issues = r.get("issues", [])
                if issues:
                    lines.append("**具体问题**:")
                    for issue in issues:
                        lines.append(f"  - {issue}")
                    lines.append("")

                evidence = r.get("evidence", [])
                if evidence:
                    lines.append("**原文证据**:")
                    for j, ev in enumerate(evidence[:2], 1):
                        lines.append(f"  > 段落{j}: {ev[:200]}...")
                    lines.append("")

                lines.append("---")
                lines.append("")
        else:
            lines.append("## ✅ 所有引用均忠实于原文，未发现问题。")
            lines.append("")

        return "\n".join(lines)


class CheckClaimTool(Tool):
    """
    单句级引用忠实度校验（用户主动触发）。

    用户写了一句带 [N] 的话（如"MIMO 技术提升30%吞吐量[1]"），
    本工具自动在文献 [N] 的向量库中检索证据，由 LLM 判决是否忠实。
    需要先用 index_literature 为对应文献建立索引。
    """

    name = "check_claim"
    description = (
        "校验一句带引用标记的主张是否忠实于原文文献。"
        "例如用户写了'MIMO技术提升30%吞吐量[1]'，本工具会在文献[1]中检索并判断是否准确。"
        "需要先用 index_literature 索引对应文献。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "claim": {
                "type": "string",
                "description": "带引用标记的主张句，如 'MIMO 技术可提升30%吞吐量[1]'",
            },
            "top_k": {
                "type": "integer",
                "description": "每个引用检索的原文段落数量，默认 3",
            },
        },
        "required": ["claim"],
    }

    def __init__(self, llm=None):
        self._llm = llm

    def execute(self, claim: str, top_k: int = 3, **kwargs) -> str:
        from tools.rag import _literature_stores, _literature_meta, _get_embed_client

        if self._llm is None:
            return "❌ LLM 未注入，无法执行忠实度判决"

        # 1. 提取引用编号
        matches = list(_CITE_PATTERN.finditer(claim))
        if not matches:
            return "ℹ️ 未检测到引用标记（如 [1]）。请确保主张句中包含方括号引用编号。"

        ref_keys = set()
        for m in matches:
            inner = m.group(1)
            for part in re.split(r'[,，]', inner):
                part = part.strip()
                range_match = re.match(r'(\d+)\s*[-~～\u2013\u2014]\s*(\d+)', part)
                if range_match:
                    for n in range(int(range_match.group(1)), int(range_match.group(2)) + 1):
                        ref_keys.add(str(n))
                elif part.isdigit():
                    ref_keys.add(part)

        if not ref_keys:
            return "ℹ️ 未能从引用标记中解析出有效编号。"

        # 2. 逐个引用编号检索 + 判决
        results = []
        for rk in sorted(ref_keys, key=int):
            if rk not in _literature_stores:
                results.append(f"⏭️ [{rk}] 文献未索引，跳过。请先 index_literature。")
                continue

            store = _literature_stores[rk]
            title = _literature_meta.get(rk, {}).get("title", "")

            # 检索
            try:
                embed_client = _get_embed_client()
                query_embedding = embed_client.embed(claim)
                evidence = store.search(query_embedding, top_k=top_k)
            except Exception as e:
                results.append(f"❌ [{rk}] 检索失败: {e}")
                continue

            if not evidence:
                results.append(f"🚫 [{rk}]{' ' + title if title else ''} — 原文中未找到相关段落，引用可能不准确。")
                continue

            # LLM 判决（复用 VerifyCitationsTool 的方法）
            verdict = self._verify_one(claim, rk, evidence)

            # 格式化结果
            icon = {
                "FAITHFUL": "✅",
                "MINOR_ISSUE": "⚠️",
                "MAJOR_ISSUE": "❌",
                "UNSUPPORTED": "🚫",
            }.get(verdict.get("verdict", ""), "❓")

            header = f"{icon} [{rk}]{' ' + title if title else ''} — {verdict.get('verdict', 'UNKNOWN')}"
            parts = [header]
            if verdict.get("analysis"):
                parts.append(f"  分析: {verdict['analysis']}")
            issues = verdict.get("issues", [])
            if issues:
                for iss in issues:
                    parts.append(f"  - {iss}")
            # 附最相关原文片段
            if evidence:
                snippet = evidence[0]["chunk"][:200]
                parts.append(f"  原文: {snippet}...")

            results.append("\n".join(parts))

        return "\n\n".join(results)

    def _verify_one(self, claim: str, ref_key: str, evidence: list[dict]) -> dict:
        """调用 LLM 做单条忠实度判决"""
        import json as _json
        from core.schema import Message, Role

        evidence_text = ""
        for i, ev in enumerate(evidence, 1):
            score_pct = round(ev.get("score", 0) * 100, 1)
            evidence_text += f"### 段落 {i}（相关度: {score_pct}%）\n{ev['chunk']}\n\n"

        prompt = _VERIFICATION_PROMPT.format(
            claim=claim,
            ref_key=ref_key,
            evidence=evidence_text,
        )

        try:
            response = self._llm.chat([
                Message(role=Role.SYSTEM, content="你是一位严格的学术审稿人。只输出 JSON。"),
                Message(role=Role.USER, content=prompt),
            ])
            resp_text = response.content or ""
            json_match = re.search(r'\{[\s\S]*\}', resp_text)
            if json_match:
                parsed = _json.loads(json_match.group())
                return {
                    "verdict": parsed.get("verdict", "UNKNOWN"),
                    "confidence": parsed.get("confidence", 0.0),
                    "analysis": parsed.get("analysis", ""),
                    "issues": parsed.get("issues", []),
                }
        except Exception as e:
            logger.warning("[CheckClaim] LLM 判决失败: %s", e)

        return {"verdict": "ERROR", "confidence": 0.0, "analysis": "LLM 判决调用失败", "issues": []}
