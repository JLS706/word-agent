# -*- coding: utf-8 -*-
"""
DocMaster Agent - RAG 工具（检索增强生成）

为 Agent 提供两个工具：
  - index_document:  解析 Word 文档 → 分块 → 生成 Embedding → 缓存
  - search_document: 查询 → 向量检索 → 返回最相关段落

这就是 RAG 的"R"（Retrieval）——让 Agent 能基于文档原文回答问题，
而不是靠 LLM 自己的记忆（可能产生幻觉）。
"""

import os
import re
from typing import Optional
from tools.base import Tool as BaseTool


# 全局向量存储（同一个文档在会话中复用）
_current_store = None
_current_embed_client = None

# ── 多文献库：按引用编号管理独立 VectorStore ──
_literature_stores: dict[str, "VectorStore"] = {}   # key = "1", "2", ...
_literature_meta: dict[str, dict] = {}               # {"1": {"title": "...", "path": "...", "chunks": N}}


def _get_embed_client():
    """延迟初始化 Embedding 客户端"""
    global _current_embed_client
    if _current_embed_client is None:
        import toml
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "config.toml"
        )
        config = toml.load(config_path)
        llm_config = config.get("llm", {})

        from core.embeddings import EmbeddingClient
        _current_embed_client = EmbeddingClient(
            api_key=llm_config.get("api_key", ""),
            base_url=llm_config.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai/"),
            model=llm_config.get("embedding_model", "gemini-embedding-001"),
        )
    return _current_embed_client


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[dict]:
    """
    将长文本分块（Chunking）。
    
    分块策略：
      1. 先按段落分（双换行符）
      2. 如果段落太长，再按 chunk_size 切割
      3. 相邻块有 overlap 字重叠（保证语义连贯）
    
    Args:
        text: 完整文本
        chunk_size: 每块最大字符数
        overlap: 相邻块重叠字符数
    
    Returns:
        [{"text": "...", "index": 0, "start_char": 0}, ...]
    """
    # 按段落分
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks = []
    current_chunk = ""
    chunk_index = 0

    for para in paragraphs:
        # 如果当前块 + 新段落没超限，合并
        if len(current_chunk) + len(para) + 1 <= chunk_size:
            current_chunk = (current_chunk + "\n" + para).strip()
        else:
            # 保存当前块
            if current_chunk:
                chunks.append({
                    "text": current_chunk,
                    "index": chunk_index,
                })
                chunk_index += 1

            # 如果单个段落就超 chunk_size，强制切割
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size - overlap):
                    sub = para[i:i + chunk_size]
                    if sub.strip():
                        chunks.append({
                            "text": sub.strip(),
                            "index": chunk_index,
                        })
                        chunk_index += 1
                current_chunk = ""
            else:
                current_chunk = para

    # 别忘了最后一块
    if current_chunk.strip():
        chunks.append({
            "text": current_chunk.strip(),
            "index": chunk_index,
        })

    return chunks


def _read_docx_text(file_path: str) -> str:
    """从 Word 文档中提取纯文本"""
    try:
        from docx import Document
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except ImportError:
        # 回退到 win32com（Windows 环境）
        try:
            import win32com.client
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(os.path.abspath(file_path))
            text = doc.Content.Text
            doc.Close(False)
            word.Quit()
            return text
        except Exception as e:
            raise RuntimeError(f"无法读取文档: {e}。请安装 python-docx: pip install python-docx")


def _read_pdf_text(file_path: str) -> str:
    """从 PDF 文件中提取纯文本（依赖 PyMuPDF）"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("需要安装 PyMuPDF 库: pip install PyMuPDF")

    doc = fitz.open(file_path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def _read_text(file_path: str) -> str:
    """根据扩展名自动选择读取方式（.pdf / .docx / .doc）"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return _read_pdf_text(file_path)
    return _read_docx_text(file_path)


class IndexDocumentTool(BaseTool):
    """
    索引文档工具 — RAG 的第一步。
    
    将 Word 文档解析为段落块，生成 Embedding 向量，
    存入内存向量库并缓存到本地 JSON 文件。
    """

    name = "index_document"
    description = (
        "为 Word 文档建立向量索引，使其可以被语义搜索。"
        "这是使用 search_document 工具之前的必要步骤。"
        "索引会自动缓存，同一文档不会重复索引。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word 文档路径（.docx）",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, **kwargs) -> str:
        global _current_store

        if not os.path.exists(file_path):
            return f"文件不存在: {file_path}"

        from core.embeddings import VectorStore

        # 检查是否有缓存
        cache_path = VectorStore.get_cache_path(file_path)
        cached = VectorStore.load_cache(cache_path)
        if cached and cached._source_file == os.path.abspath(file_path):
            _current_store = cached
            return (
                f"已从缓存加载文档索引（{len(cached)} 个段落块）。"
                f"可以使用 search_document 工具进行语义搜索了。"
            )

        # 读取文档
        try:
            text = _read_text(file_path)
        except Exception as e:
            return f"读取文档失败: {e}"

        if not text.strip():
            return "文档内容为空"

        # 分块（优先语义切块，回退到机械切块）
        try:
            from core.semantic_chunker import semantic_chunk
            embed_client = _get_embed_client()
            chunk_dicts = semantic_chunk(text, embed_client)
            if not chunk_dicts:
                # 语义切块返回空，回退
                chunk_dicts = _chunk_text(text, chunk_size=500, overlap=50)
        except Exception:
            # 语义切块失败，回退到机械切块
            chunk_dicts = _chunk_text(text, chunk_size=500, overlap=50)

        if not chunk_dicts:
            return "文档分块后为空"

        chunk_texts = [c["text"] for c in chunk_dicts]
        chunk_metadata = [{"index": c["index"]} for c in chunk_dicts]

        # 生成 Embedding
        try:
            embed_client = _get_embed_client()
            embeddings = embed_client.embed_batch(chunk_texts)
        except Exception as e:
            return f"生成 Embedding 失败: {e}"

        # 存入向量库
        store = VectorStore()
        store._source_file = os.path.abspath(file_path)
        store.add(chunk_texts, embeddings, chunk_metadata)

        # 缓存到文件
        try:
            store.save_cache(cache_path)
        except Exception:
            pass  # 缓存失败不影响主流程

        _current_store = store

        return (
            f"文档索引完成！共 {len(chunk_texts)} 个段落块，"
            f"每块约 500 字，向量维度 {len(embeddings[0])}。"
            f"现在可以使用 search_document 工具进行语义搜索了。"
        )


class SearchDocumentTool(BaseTool):
    """
    语义搜索工具 — RAG 的核心。
    
    将查询文本转为向量，在文档向量库中
    找到最相似的段落，返回给 Agent 作为上下文。
    """

    name = "search_document"
    description = (
        "在已索引的文档中进行语义搜索，返回与查询最相关的段落。"
        "使用前必须先用 index_document 工具索引文档。"
        "返回的段落可以作为回答用户问题的依据，减少幻觉。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询（自然语言，如'MIMO技术的应用场景'）",
            },
            "top_k": {
                "type": "integer",
                "description": "返回最相关的段落数量，默认 3",
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str, top_k: int = 3, **kwargs) -> str:
        if _current_store is None or len(_current_store) == 0:
            return "尚未索引任何文档，请先使用 index_document 工具索引文档。"

        # 查询文本 → Embedding
        try:
            embed_client = _get_embed_client()
            query_embedding = embed_client.embed(query)
        except Exception as e:
            return f"查询 Embedding 生成失败: {e}"

        # 向量检索
        results = _current_store.search(query_embedding, top_k=top_k)

        if not results:
            return "未找到相关内容。"

        # 格式化输出
        output_parts = [f"找到 {len(results)} 个相关段落（按相关度排序）：\n"]
        for i, r in enumerate(results, 1):
            score_pct = round(r["score"] * 100, 1)
            output_parts.append(
                f"--- 段落 {i} (相关度: {score_pct}%) ---\n"
                f"{r['chunk']}\n"
            )

        return "\n".join(output_parts)


# ══════════════════════════════════════════════════════════
# 多文献库工具（Literature RAG）
# ══════════════════════════════════════════════════════════

def _index_one_literature(ref_key: str, file_path: str, label: str = "") -> str:
    """
    索引单篇文献到 _literature_stores，返回结果描述。
    内部共享函数，供 IndexLiteratureTool 和 citation_verifier 复用。
    """
    from core.embeddings import VectorStore

    abs_path = os.path.abspath(file_path)
    cache_path = VectorStore.get_cache_path(abs_path)

    # 尝试从缓存加载
    cached = VectorStore.load_cache(cache_path)
    if cached and cached._source_file == abs_path:
        _literature_stores[ref_key] = cached
        _literature_meta[ref_key] = {
            "title": label or os.path.basename(file_path),
            "path": abs_path,
            "chunks": len(cached),
        }
        return f"文献 [{ref_key}] 从缓存加载（{len(cached)} 个段落块）"

    # 读取文本
    text = _read_text(file_path)
    if not text.strip():
        raise ValueError(f"文献 [{ref_key}] 内容为空")

    # 分块
    embed_client = _get_embed_client()
    try:
        from core.semantic_chunker import semantic_chunk
        chunk_dicts = semantic_chunk(text, embed_client)
        if not chunk_dicts:
            chunk_dicts = _chunk_text(text, chunk_size=500, overlap=50)
    except Exception:
        chunk_dicts = _chunk_text(text, chunk_size=500, overlap=50)

    chunk_texts = [c["text"] for c in chunk_dicts]
    chunk_metadata = [{"index": c["index"], "ref_key": ref_key} for c in chunk_dicts]

    embeddings = embed_client.embed_batch(chunk_texts)

    store = VectorStore()
    store._source_file = abs_path
    store.add(chunk_texts, embeddings, chunk_metadata)

    try:
        store.save_cache(cache_path)
    except Exception:
        pass

    _literature_stores[ref_key] = store
    _literature_meta[ref_key] = {
        "title": label or os.path.basename(file_path),
        "path": abs_path,
        "chunks": len(store),
    }
    return f"文献 [{ref_key}] 索引完成（{len(store)} 个段落块）"


class IndexLiteratureTool(BaseTool):
    """索引单篇文献并关联引用编号，支持 PDF 和 Word。"""

    name = "index_literature"
    description = (
        "为一篇参考文献建立向量索引，并关联引用编号（如 [1]）。"
        "支持 .pdf 和 .docx 格式。索引后可用 search_literature 在该文献中语义检索，"
        "也可用 check_claim 校验引用忠实度。索引会自动缓存，同一文献不会重复索引。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "文献文件路径（.pdf 或 .docx）",
            },
            "ref_key": {
                "type": "string",
                "description": "引用编号，如 '1' 对应正文中的 [1]",
            },
            "label": {
                "type": "string",
                "description": "文献的简短标签/标题（可选，如 'MIMO综述'）",
            },
        },
        "required": ["file_path", "ref_key"],
    }

    def execute(self, file_path: str, ref_key: str, label: str = "", **kwargs) -> str:
        ref_key = str(ref_key).strip()
        if not os.path.exists(file_path):
            return f"❌ 文件不存在: {file_path}"

        try:
            msg = _index_one_literature(ref_key, file_path, label)
        except Exception as e:
            return f"❌ 索引失败: {e}"

        return (
            f"✅ {msg}。\n"
            f"现在可以用 search_literature(ref_key=\"{ref_key}\") 搜索该文献，"
            f"或用 check_claim 校验引用忠实度。"
        )


class SearchLiteratureTool(BaseTool):
    """在已索引的文献库中语义搜索。"""

    name = "search_literature"
    description = (
        "在已索引的参考文献中进行语义搜索。"
        "可指定 ref_key 搜索单篇文献，或留空进行跨全库搜索。"
        "使用前需先用 index_literature 索引文献。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询（自然语言）",
            },
            "ref_key": {
                "type": "string",
                "description": "指定文献编号搜索（如 '1'），留空则跨全库搜索",
            },
            "top_k": {
                "type": "integer",
                "description": "返回最相关的段落数量，默认 3",
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str, ref_key: str = "", top_k: int = 3, **kwargs) -> str:
        if not _literature_stores:
            return "❌ 尚未索引任何文献，请先使用 index_literature 工具。"

        ref_key = str(ref_key).strip() if ref_key else ""

        # 确定搜索范围
        if ref_key:
            if ref_key not in _literature_stores:
                return f"❌ 文献 [{ref_key}] 未索引。已索引: {list(_literature_stores.keys())}"
            stores_to_search = {ref_key: _literature_stores[ref_key]}
        else:
            stores_to_search = _literature_stores

        try:
            embed_client = _get_embed_client()
            query_embedding = embed_client.embed(query)
        except Exception as e:
            return f"❌ Embedding 生成失败: {e}"

        # 搜索所有目标 store，合并结果
        all_results = []
        for rk, store in stores_to_search.items():
            hits = store.search(query_embedding, top_k=top_k)
            for h in hits:
                h["ref_key"] = rk
                h["title"] = _literature_meta.get(rk, {}).get("title", "")
            all_results.extend(hits)

        # 按分数降序，取 top_k
        all_results.sort(key=lambda x: x["score"], reverse=True)
        all_results = all_results[:top_k]

        if not all_results:
            return "未找到相关内容。"

        output = [f"找到 {len(all_results)} 个相关段落：\n"]
        for i, r in enumerate(all_results, 1):
            score_pct = round(r["score"] * 100, 1)
            src = f"[{r['ref_key']}] {r['title']}" if r.get("title") else f"[{r['ref_key']}]"
            output.append(
                f"--- 段落 {i} (来源: {src}, 相关度: {score_pct}%) ---\n"
                f"{r['chunk']}\n"
            )
        return "\n".join(output)


class ListLiteratureTool(BaseTool):
    """列出已索引的所有文献。"""

    name = "list_literature"
    description = "列出当前已索引的所有参考文献及其编号、标题、段落数。"
    parameters = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs) -> str:
        if not _literature_meta:
            return "当前没有已索引的文献。请使用 index_literature 工具索引文献。"

        lines = [f"已索引 {len(_literature_meta)} 篇文献：\n"]
        for rk in sorted(_literature_meta.keys(), key=lambda x: int(x) if x.isdigit() else x):
            info = _literature_meta[rk]
            lines.append(
                f"  [{rk}] {info.get('title', '未知')} — "
                f"{info.get('chunks', '?')} 个段落块 — {info.get('path', '')}"
            )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# 自动绑定工具：参考文献列表 × 文件夹 → 自动匹配 + 索引
# ══════════════════════════════════════════════════════════

def _extract_refs_from_thesis(thesis_path: str) -> list[dict]:
    """
    从综述/论文中提取参考文献条目。

    返回: [{"key": "1", "text": "Zhang J, et al. Massive MIMO..."}, ...]
    """
    text = _read_text(thesis_path)
    if not text:
        return []

    # 定位参考文献段落（"参考文献" / "References" 之后）
    lines = text.split("\n")
    in_refs = False
    refs = []
    ref_pattern = re.compile(r'^\[(\d+)\]\s*(.+)')

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not in_refs:
            if ("参考文献" in line or "References" in line.title()) and len(line) < 60:
                in_refs = True
            continue
        # 遇到致谢/附录等停止
        if len(line) < 60:
            stop_keywords = ["致谢", "附录", "基金项目", "作者简介",
                             "Acknowledgment", "Appendix"]
            if any(kw.lower() in line.lower() for kw in stop_keywords):
                break
        m = ref_pattern.match(line)
        if m:
            refs.append({"key": m.group(1), "text": m.group(2).strip()})

    return refs


def _tokenize_for_match(text: str) -> set[str]:
    """提取中英文 token 用于模糊匹配（小写化，去停用词）。"""
    tokens = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]{2,}|\d{4}', text.lower()))
    # 去掉太短或太常见的词
    stop = {"the", "and", "for", "vol", "no", "pp", "in", "of", "on", "to",
            "ieee", "acm", "springer", "elsevier", "press", "journal",
            "trans", "conference", "proceedings", "international"}
    return tokens - stop


def _extract_pdf_title(pdf_path: str) -> str:
    """从 PDF 首页提取疑似标题（前 5 行中最长的那行）。"""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return ""
        first_page_text = doc[0].get_text()
        doc.close()
        lines = [l.strip() for l in first_page_text.split("\n") if l.strip()]
        # 启发式：前 5 行中最长的一行最可能是标题
        candidates = lines[:5] if len(lines) >= 5 else lines
        if not candidates:
            return ""
        return max(candidates, key=len)
    except Exception:
        return ""


def _match_score(ref_tokens: set[str], candidate_tokens: set[str]) -> float:
    """Jaccard-like 匹配得分，值域 [0, 1]。"""
    if not ref_tokens or not candidate_tokens:
        return 0.0
    overlap = ref_tokens & candidate_tokens
    # 加权：用 overlap / min(len) 而不是 Jaccard，因为文件名通常比引用文本短很多
    return len(overlap) / max(min(len(ref_tokens), len(candidate_tokens)), 1)


class AutoBindLiteratureTool(BaseTool):
    """
    自动绑定：读取论文参考文献列表 + 扫描文献文件夹 → 模糊匹配 → 自动索引。

    省去用户逐篇手动 index_literature 的操作。
    """

    name = "auto_bind_literature"
    description = (
        "自动将论文参考文献列表与本地文献文件夹中的 PDF/Word 文件匹配并索引。"
        "读取论文中的 [1], [2], ... 参考文献条目，扫描指定文件夹中的 .pdf/.docx 文件，"
        "通过标题/作者/年份模糊匹配，自动调用 index_literature 建立索引。"
        "匹配结果会列出，用户可以确认或手动修正。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "thesis_path": {
                "type": "string",
                "description": "用户的综述/论文路径（从中提取参考文献列表）",
            },
            "literature_folder": {
                "type": "string",
                "description": "存放参考文献 PDF/Word 文件的文件夹路径",
            },
            "threshold": {
                "type": "number",
                "description": "匹配阈值（0-1），低于此值视为未匹配，默认 0.3",
            },
        },
        "required": ["thesis_path", "literature_folder"],
    }

    def execute(
        self,
        thesis_path: str,
        literature_folder: str,
        threshold: float = 0.3,
        **kwargs,
    ) -> str:
        if not os.path.exists(thesis_path):
            return f"❌ 论文文件不存在: {thesis_path}"
        if not os.path.isdir(literature_folder):
            return f"❌ 文献文件夹不存在: {literature_folder}"

        self.report_progress(5, "提取参考文献列表...")

        # 1. 从论文中提取参考文献
        refs = _extract_refs_from_thesis(thesis_path)
        if not refs:
            return "❌ 未能从论文中提取到参考文献条目（需要有 [1], [2]... 格式的参考文献段落）。"

        self.report_progress(15, f"提取到 {len(refs)} 条参考文献，扫描文献文件夹...")

        # 2. 扫描文件夹中的 PDF/Word 文件
        supported_ext = {".pdf", ".docx", ".doc"}
        files = []
        for fname in os.listdir(literature_folder):
            ext = os.path.splitext(fname)[1].lower()
            if ext in supported_ext:
                full_path = os.path.join(literature_folder, fname)
                files.append(full_path)

        if not files:
            return f"❌ 文件夹 {literature_folder} 中未找到 PDF/Word 文件。"

        self.report_progress(20, f"找到 {len(files)} 个文献文件，开始匹配...")

        # 3. 为每个文件预计算 token（文件名 + PDF标题）
        file_tokens: dict[str, set[str]] = {}
        file_titles: dict[str, str] = {}
        for fp in files:
            name_no_ext = os.path.splitext(os.path.basename(fp))[0]
            tokens = _tokenize_for_match(name_no_ext)
            title = ""
            if fp.lower().endswith(".pdf"):
                title = _extract_pdf_title(fp)
                if title:
                    tokens |= _tokenize_for_match(title)
            file_tokens[fp] = tokens
            file_titles[fp] = title or name_no_ext

        # 4. 对每条参考文献做匹配
        bindings = []       # (ref_key, ref_text_short, matched_path, score)
        unmatched = []       # (ref_key, ref_text_short)
        used_files = set()

        for ref in refs:
            ref_tokens = _tokenize_for_match(ref["text"])
            best_path, best_score = "", 0.0

            for fp, ftokens in file_tokens.items():
                if fp in used_files:
                    continue
                score = _match_score(ref_tokens, ftokens)
                if score > best_score:
                    best_score = score
                    best_path = fp

            ref_short = ref["text"][:80] + ("..." if len(ref["text"]) > 80 else "")

            if best_score >= threshold and best_path:
                bindings.append((ref["key"], ref_short, best_path, best_score))
                used_files.add(best_path)
            else:
                unmatched.append((ref["key"], ref_short))

        self.report_progress(50, f"匹配完成：{len(bindings)} 匹配 / {len(unmatched)} 未匹配，开始索引...")

        # 5. 自动索引匹配到的文献
        indexed = []
        failed = []
        for i, (rk, ref_short, fp, score) in enumerate(bindings):
            pct = 50 + int(45 * i / max(len(bindings), 1))
            self.report_progress(pct, f"索引 [{rk}]...")
            try:
                label = file_titles.get(fp, os.path.basename(fp))
                _index_one_literature(rk, fp, label)
                indexed.append((rk, ref_short, fp, score))
            except Exception as e:
                failed.append((rk, ref_short, str(e)))

        self.report_progress(98, "生成报告...")

        # 6. 输出报告
        lines = [f"# 📚 文献自动绑定报告\n"]
        lines.append(f"**论文**: {os.path.basename(thesis_path)}")
        lines.append(f"**文献夹**: {literature_folder}")
        lines.append(f"**参考文献**: {len(refs)} 条 | **文件**: {len(files)} 个\n")

        if indexed:
            lines.append(f"## ✅ 成功绑定并索引（{len(indexed)} 篇）\n")
            for rk, ref_short, fp, score in indexed:
                pct = round(score * 100)
                lines.append(f"  [{rk}] → {os.path.basename(fp)} (匹配度 {pct}%)")
                lines.append(f"      参考文献: {ref_short}")

        if failed:
            lines.append(f"\n## ❌ 索引失败（{len(failed)} 篇）\n")
            for rk, ref_short, err in failed:
                lines.append(f"  [{rk}] {ref_short}")
                lines.append(f"      错误: {err}")

        if unmatched:
            lines.append(f"\n## ⏭️ 未匹配（{len(unmatched)} 篇）\n")
            lines.append("  以下参考文献未在文件夹中找到匹配文件，可手动用 index_literature 绑定：\n")
            for rk, ref_short in unmatched:
                lines.append(f"  [{rk}] {ref_short}")

        return "\n".join(lines)
