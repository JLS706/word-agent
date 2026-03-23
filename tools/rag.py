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
from tools.base import BaseTool


# 全局向量存储（同一个文档在会话中复用）
_current_store = None
_current_embed_client = None


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
            model=llm_config.get("embedding_model", "text-embedding-3-small"),
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

    def run(self, file_path: str, **kwargs) -> str:
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
            text = _read_docx_text(file_path)
        except Exception as e:
            return f"读取文档失败: {e}"

        if not text.strip():
            return "文档内容为空"

        # 分块
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

    def run(self, query: str, top_k: int = 3, **kwargs) -> str:
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
