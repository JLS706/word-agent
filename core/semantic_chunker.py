# -*- coding: utf-8 -*-
"""
DocMaster Agent - 语义切块 (Semantic Chunking)

摒弃传统的按字数机械切块，采用基于 Embedding 相似度的语义切块：
  1. 按标点切分成句子
  2. 计算相邻句子的 Embedding 向量相似度
  3. 当相似度出现"断崖式下跌"时，标记为切块边界
  4. 自适应阈值：均值 - 1σ（标准差）

优势：
  - 不会把同一个段落拆散
  - 切出的块在语义上是完整的
  - 面试可以说"我实现了基于 Embedding 的语义切块"

回退方案：
  - 如果 Embedding 客户端不可用，回退到 rag.py 的 _chunk_text()
"""

import re
import numpy as np
from typing import Optional

from core.logger import logger


def _split_sentences(text: str) -> list[str]:
    """
    按中英文标点将文本切分为句子。

    切分策略：
      1. 先按"句号、问号、叹号"切分（中英文各自的标点）
      2. 过滤掉过短的碎片（< 10 字符）
      3. 合并过短的句子到前一句（避免碎片化）

    Args:
        text: 输入文本

    Returns:
        句子列表
    """
    # 按中英文句末标点切分，保留标点
    raw_parts = re.split(r'(?<=[。！？\.!\?])\s*', text)
    raw_parts = [p.strip() for p in raw_parts if p.strip()]

    if not raw_parts:
        return [text.strip()] if text.strip() else []

    # 合并过短的碎片
    sentences = []
    buffer = ""
    for part in raw_parts:
        buffer = (buffer + " " + part).strip() if buffer else part
        if len(buffer) >= 10:
            sentences.append(buffer)
            buffer = ""

    # 末尾剩余
    if buffer:
        if sentences:
            sentences[-1] += " " + buffer
        else:
            sentences.append(buffer)

    return sentences


def _detect_boundaries(
    similarities: list[float],
    threshold: Optional[float] = None,
) -> list[int]:
    """
    检测相似度序列中的"断崖式下跌"位置。

    自适应阈值算法：
      - 计算相似度的均值 μ 和标准差 σ
      - 阈值 = μ - 1σ
      - 低于阈值的位置标记为切块边界

    Args:
        similarities: 相邻句子对的余弦相似度列表
        threshold: 手动指定阈值（None 则使用自适应）

    Returns:
        切块边界的索引列表（指的是在 similarities 中的索引）
    """
    if not similarities:
        return []

    arr = np.array(similarities)

    if threshold is None:
        # 自适应阈值：均值 - 1 倍标准差
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        threshold = mean - std
        # 兜底：阈值不能低于 0.1（防止标准差过大导致切不出边界）
        threshold = max(threshold, 0.1)

    boundaries = []
    for i, sim in enumerate(similarities):
        if sim < threshold:
            boundaries.append(i)

    return boundaries


def semantic_chunk(
    text: str,
    embed_client,
    threshold: float = None,
    max_chunk_size: int = 800,
    min_chunk_size: int = 50,
) -> list[dict]:
    """
    基于 Embedding 相似度的语义切块。

    算法流程：
      1. 将文本按标点切成句子
      2. 批量生成所有句子的 Embedding 向量
      3. 计算相邻句子的余弦相似度
      4. 在相似度"断崖式下跌"处切分
      5. 合并过小的 chunk，拆分过大的 chunk

    Args:
        text: 输入的长文本
        embed_client: EmbeddingClient 实例
        threshold: 切分阈值（None 则自适应）
        max_chunk_size: 单个 chunk 的最大字符数
        min_chunk_size: 单个 chunk 的最小字符数

    Returns:
        [{"text": "...", "index": 0}, {"text": "...", "index": 1}, ...]
    """
    # Step 1: 切分句子
    sentences = _split_sentences(text)

    if len(sentences) <= 1:
        # 文本太短，只有一句话
        return [{"text": text.strip(), "index": 0}] if text.strip() else []

    # Step 2: 批量计算 Embedding
    try:
        embeddings = embed_client.embed_batch(sentences)
    except Exception as e:
        logger.warning("[SemanticChunk] Embedding 失败，回退到段落切块: %s", e)
        return _fallback_chunk(text, max_chunk_size)

    if len(embeddings) != len(sentences):
        logger.warning("[SemanticChunk] Embedding 数量不匹配，回退")
        return _fallback_chunk(text, max_chunk_size)

    # Step 3: 计算相邻句子的余弦相似度
    from core.embeddings import cosine_similarity as cos_sim

    similarities = []
    for i in range(len(embeddings) - 1):
        vec_a = np.array(embeddings[i])
        vec_b = np.array(embeddings[i + 1])
        sim = cos_sim(vec_a, vec_b)
        similarities.append(sim)

    # Step 4: 检测断崖边界
    boundaries = _detect_boundaries(similarities, threshold)

    if logger.isEnabledFor(10):  # DEBUG level
        logger.debug(
            "[SemanticChunk] %d 句子, %d 个边界, 阈值 %.3f",
            len(sentences), len(boundaries),
            threshold if threshold else -1,
        )

    # Step 5: 按边界切分成 chunk
    chunks = []
    current_sentences = []
    boundary_set = set(boundaries)

    for i, sentence in enumerate(sentences):
        current_sentences.append(sentence)

        # 如果当前位置是边界，或者是最后一句
        if i in boundary_set or i == len(sentences) - 1:
            chunk_text = "".join(current_sentences)
            if chunk_text.strip():
                chunks.append(chunk_text.strip())
            current_sentences = []

    # Step 6: 后处理 — 合并过小块，拆分过大块
    chunks = _post_process_chunks(chunks, max_chunk_size, min_chunk_size)

    # 构造返回格式
    result = []
    for i, chunk in enumerate(chunks):
        result.append({"text": chunk, "index": i})

    logger.info(
        "[SemanticChunk] 语义切块完成: %d 句 → %d 块",
        len(sentences), len(result),
    )

    return result


def _post_process_chunks(
    chunks: list[str],
    max_size: int,
    min_size: int,
) -> list[str]:
    """
    后处理：合并过小块 + 拆分过大块。

    规则：
      - 小于 min_size 的块合并到前一块
      - 大于 max_size 的块按段落或字数强制拆分
    """
    if not chunks:
        return chunks

    # Pass 1: 合并过小的块
    merged = []
    for chunk in chunks:
        if merged and len(chunk) < min_size:
            merged[-1] += "\n" + chunk
        else:
            merged.append(chunk)

    # Pass 2: 拆分过大的块
    final = []
    for chunk in merged:
        if len(chunk) <= max_size:
            final.append(chunk)
        else:
            # 按段落拆分
            paragraphs = chunk.split("\n")
            current = ""
            for para in paragraphs:
                if len(current) + len(para) + 1 <= max_size:
                    current = (current + "\n" + para).strip()
                else:
                    if current:
                        final.append(current)
                    # 单个段落仍超长 → 硬切
                    if len(para) > max_size:
                        for j in range(0, len(para), max_size):
                            sub = para[j:j + max_size].strip()
                            if sub:
                                final.append(sub)
                        current = ""
                    else:
                        current = para
            if current:
                final.append(current)

    return final


def _fallback_chunk(text: str, chunk_size: int = 500) -> list[dict]:
    """
    回退方案：按段落 + 字数的简单切块。
    当 Embedding 不可用时使用。
    """
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 <= chunk_size:
            current = (current + "\n" + para).strip()
        else:
            if current:
                chunks.append({"text": current, "index": len(chunks)})
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size):
                    sub = para[i:i + chunk_size].strip()
                    if sub:
                        chunks.append({"text": sub, "index": len(chunks)})
                current = ""
            else:
                current = para

    if current:
        chunks.append({"text": current, "index": len(chunks)})

    return chunks
