# -*- coding: utf-8 -*-
"""
DocMaster Agent - 语义切块 (Semantic Chunking)

摒弃传统的按字数机械切块，采用基于 Embedding 相似度的语义切块：
  1. 鲁棒分句：保护小数点(3.14)、缩写(U.S.A.)、学术引用(Fig. 1)不被误切
  2. 短句合并为基础语义单元（Base Text Unit, ≥100字），稳定 Embedding 质量
  3. 计算相邻单元的余弦距离，滑动窗口平滑消除过渡句噪音
  4. μ + c·σ 异常检测：仅在距离显著偏离均值时切刀（Semantic Cliff Detection）
  5. 后处理：合并过小块，拆分过大块

算法参考：
  - 边界检测公式: d(v_i, v_{i+1}) = 1 - cos(v_i, v_{i+1})
  - 切刀条件: smoothed_d > μ + c·σ （c 默认为 1.0，约保留 16% 的极端断层）

回退方案：
  - 如果 Embedding 客户端不可用，回退到 rag.py 的 _chunk_text()
"""

import re
import numpy as np

from core.logger import logger


# 保护模式：这些正则模式匹配的 "." 不是句末标点，需要临时替换以防误切
_PROTECT_PATTERNS = [
    # 小数点: 3.14, 0.05, 1e-3.5
    (re.compile(r'(\d)\.(\d)'), r'\1<DOT>\2'),
    # 大写缩写: U.S.A., D.C., A.I. (中间的点)
    (re.compile(r'([A-Z])\.([A-Z])'), r'\1<DOT>\2'),
    # 大写缩写尾点: U.S.A. is → A<DOT> is（最后一个点后跟空格+小写字母）
    (re.compile(r'([A-Z])\.(\s+[a-z])'), lambda m: m.group(1) + '<DOT>' + m.group(2)),
    # 学术常见缩写: Fig. Eq. Ref. Vol. No. Dr. Mr. Mrs. vs. etc. al.
    (re.compile(
        r'\b(Fig|Eq|Ref|Vol|No|Dr|Mr|Mrs|Prof|Jr|Sr|vs|etc|al|approx'
        r'|dept|est|govt|inc|corp|ltd|assn|natl|intl)\.',
        re.IGNORECASE,
    ), lambda m: m.group(0).replace('.', '<DOT>')),
    # 拉丁缩写: e.g. i.e. cf. et al.
    (re.compile(r'\b(e\.g|i\.e|cf|viz|ca)\.',
                re.IGNORECASE), lambda m: m.group(0).replace('.', '<DOT>')),
]

_SENTINEL = '<DOT>'


def _split_sentences(text: str) -> list[str]:
    """
    鲁棒分句器：保护小数点、缩写、学术引用不被误切。

    策略（三步）：
      1. 保护区标记：把不是句末的 "." 替换为 <DOT> 占位符
      2. 在真正的句末标点处切分（。！？.!?）
      3. 还原占位符，合并过短碎片

    Args:
        text: 输入文本

    Returns:
        句子列表
    """
    # ── Step 1: 保护区标记 ──
    protected = text
    for pattern, repl in _PROTECT_PATTERNS:
        protected = pattern.sub(repl, protected)

    # ── Step 2: 在真正的句末标点处切分 ──
    raw_parts = re.split(r'(?<=[。！？.!?])\s+', protected)
    raw_parts = [p.strip() for p in raw_parts if p.strip()]

    if not raw_parts:
        return [text.strip()] if text.strip() else []

    # ── Step 3: 还原保护区 + 合并碎片 ──
    sentences = []
    buffer = ""
    for part in raw_parts:
        restored = part.replace(_SENTINEL, '.')
        buffer = (buffer + " " + restored).strip() if buffer else restored
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


def _merge_to_base_units(sentences: list[str], min_unit_len: int = 100) -> list[str]:
    """
    将短句合并为基础语义单元（Base Text Unit）。

    单独一句话的 Embedding 语义信息太弱（特别是过渡句、短句），
    合并后的语义单元 Embedding 更稳定，减少相似度计算的噪音。

    Args:
        sentences: 分句后的句子列表
        min_unit_len: 基础语义单元的最小字符数（默认 100）

    Returns:
        合并后的语义单元列表
    """
    units = []
    buffer = ""
    for s in sentences:
        buffer = (buffer + " " + s).strip() if buffer else s
        if len(buffer) >= min_unit_len:
            units.append(buffer)
            buffer = ""
    # 末尾剩余
    if buffer:
        if units:
            units[-1] += " " + buffer
        else:
            units.append(buffer)
    return units


def _detect_boundaries(
    similarities: list[float],
    sensitivity: float = 1.0,
    window_size: int = 3,
) -> list[int]:
    """
    Semantic Cliff Detection — 滑动窗口平滑 + μ+cσ 异常检测。

    算法（三步）：
      1. 将余弦相似度转为余弦距离: d = 1 - sim
      2. 对距离序列做滑动平均平滑（消除过渡句的局部噪音）
      3. 计算平滑后距离的 μ 和 σ，超过 μ + c·σ 的位置标记为语义断层

    与原版（μ - σ 阈值 + 直接比较相似度）的改进：
      - 转化为距离空间后，"越大越应该切"的语义更直观
      - 滑动平滑消除单个过渡句造成的假边界
      - c=1.0 时约保留 16% 的极端断层（正态分布尾部）

    Args:
        similarities: 相邻语义单元的余弦相似度列表
        sensitivity: 灵敏度超参 c（越小切得越多，越大切得越少）
        window_size: 滑动平滑窗口大小（奇数效果更好）

    Returns:
        切块边界的索引列表
    """
    if len(similarities) < 2:
        return []

    # Step 1: 余弦相似度 → 余弦距离
    distances = np.array([1.0 - s for s in similarities])

    # Step 2: 滑动窗口均值平滑（消除过渡句的局部噪音）
    if len(distances) >= window_size:
        kernel = np.ones(window_size) / window_size
        # mode='same' 保持长度一致，边界用零填充
        smoothed = np.convolve(distances, kernel, mode='same')
    else:
        smoothed = distances

    # Step 3: μ + c·σ 异常检测
    mu = float(np.mean(smoothed))
    sigma = float(np.std(smoothed))
    threshold = mu + sensitivity * sigma

    # 兜底：如果所有距离几乎相等（σ≈0），不切
    if sigma < 1e-6:
        return []

    boundaries = [i for i, d in enumerate(smoothed) if d > threshold]

    logger.debug(
        "[SemanticCliff] μ=%.4f σ=%.4f threshold=%.4f → %d boundaries",
        mu, sigma, threshold, len(boundaries),
    )

    return boundaries


def semantic_chunk(
    text: str,
    embed_client,
    sensitivity: float = 1.0,
    max_chunk_size: int = 800,
    min_chunk_size: int = 50,
    min_unit_len: int = 100,
) -> list[dict]:
    """
    基于 Embedding 相似度的语义切块（生产级实现）。

    算法流程：
      1. 鲁棒分句（保护小数点/缩写/学术引用）
      2. 短句合并为基础语义单元（≥100字，稳定 Embedding 质量）
      3. 批量生成所有语义单元的 Embedding 向量
      4. 计算相邻单元的余弦相似度
      5. Semantic Cliff Detection：滑动窗口平滑 + μ+cσ 异常检测
      6. 后处理：合并过小块，拆分过大块

    Args:
        text: 输入的长文本
        embed_client: EmbeddingClient 实例
        sensitivity: 切分灵敏度 c（越小切越多，默认 1.0）
        max_chunk_size: 单个 chunk 的最大字符数
        min_chunk_size: 单个 chunk 的最小字符数
        min_unit_len: 基础语义单元的最小字符数

    Returns:
        [{"text": "...", "index": 0}, {"text": "...", "index": 1}, ...]
    """
    # Step 1: 鲁棒分句
    sentences = _split_sentences(text)

    if len(sentences) <= 1:
        return [{"text": text.strip(), "index": 0}] if text.strip() else []

    # Step 2: 短句合并为基础语义单元
    units = _merge_to_base_units(sentences, min_unit_len)

    if len(units) <= 1:
        return [{"text": text.strip(), "index": 0}] if text.strip() else []

    # Step 3: 批量计算 Embedding
    try:
        embeddings = embed_client.embed_batch(units)
    except Exception as e:
        logger.warning("[SemanticChunk] Embedding 失败，回退到段落切块: %s", e)
        return _fallback_chunk(text, max_chunk_size)

    if len(embeddings) != len(units):
        logger.warning("[SemanticChunk] Embedding 数量不匹配，回退")
        return _fallback_chunk(text, max_chunk_size)

    # Step 4: 计算相邻语义单元的余弦相似度
    from core.embeddings import cosine_similarity as cos_sim

    similarities = []
    for i in range(len(embeddings) - 1):
        vec_a = np.array(embeddings[i])
        vec_b = np.array(embeddings[i + 1])
        sim = cos_sim(vec_a, vec_b)
        similarities.append(sim)

    # Step 5: Semantic Cliff Detection
    boundaries = _detect_boundaries(similarities, sensitivity)

    logger.debug(
        "[SemanticChunk] %d 句 → %d 语义单元 → %d 边界",
        len(sentences), len(units), len(boundaries),
    )

    # Step 6: 按边界切分成 chunk
    chunks = []
    current_units = []
    boundary_set = set(boundaries)

    for i, unit in enumerate(units):
        current_units.append(unit)

        # 如果当前位置是边界，或者是最后一个单元
        if i in boundary_set or i == len(units) - 1:
            chunk_text = " ".join(current_units)
            if chunk_text.strip():
                chunks.append(chunk_text.strip())
            current_units = []

    # Step 7: 后处理 — 合并过小块，拆分过大块
    chunks = _post_process_chunks(chunks, max_chunk_size, min_chunk_size)

    # 构造返回格式
    result = []
    for i, chunk in enumerate(chunks):
        result.append({"text": chunk, "index": i})

    logger.info(
        "[SemanticChunk] 语义切块完成: %d 句 → %d 单元 → %d 块",
        len(sentences), len(units), len(result),
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
