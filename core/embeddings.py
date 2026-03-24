# -*- coding: utf-8 -*-
"""
DocMaster Agent - Embedding & 向量检索

实现 RAG 的底层能力：
  1. 文本 → 向量 (Embedding)
  2. 向量相似度检索 (余弦相似度)
  3. 向量缓存 (JSON 持久化，避免重复调 API)

不依赖任何向量数据库，纯 numpy 实现，
面试时可以说"我理解向量检索的底层原理"。
"""

import os
import json
import hashlib
import numpy as np
from typing import Optional


class EmbeddingClient:
    """
    OpenAI Embedding API 客户端。
    
    将文本转为高维向量（1536 维），
    语义相近的文本在向量空间中距离更近。
    """

    def __init__(self, api_key: str, base_url: str, model: str = "gemini-embedding-001"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def embed(self, text: str) -> list[float]:
        """单条文本 → 向量"""
        resp = self.client.embeddings.create(
            input=text,
            model=self.model,
        )
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        批量文本 → 向量列表。
        
        OpenAI 支持一次传多条文本，比逐条调用快很多。
        但单次最多约 2048 条，这里分批处理。
        """
        all_embeddings = []
        batch_size = 100  # 每批 100 条
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self.client.embeddings.create(
                input=batch,
                model=self.model,
            )
            # 按 index 排序确保顺序正确
            sorted_data = sorted(resp.data, key=lambda x: x.index if x.index is not None else -1)
            all_embeddings.extend([d.embedding for d in sorted_data])
        return all_embeddings


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    余弦相似度：衡量两个向量的方向相似程度。
    
    值域 [-1, 1]：
      1  = 完全相同方向（语义相同）
      0  = 正交（语义无关）
      -1 = 完全相反（语义相反）
    
    公式: cos(θ) = (a · b) / (|a| × |b|)
    """
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class VectorStore:
    """
    内存向量存储 + JSON 文件缓存。
    
    核心数据结构：
      chunks:     ["段落1文本", "段落2文本", ...]
      embeddings: [[0.01, -0.03, ...], [0.02, 0.05, ...], ...]  # 每个是 1536 维向量
    
    检索时：query → embedding → 与所有 chunk 的 embedding 计算余弦相似度 → 返回 Top-K
    """

    def __init__(self):
        self.chunks: list[str] = []
        self.embeddings: np.ndarray = np.array([])  # shape: (n_chunks, embed_dim)
        self.metadata: list[dict] = []  # 每个 chunk 的元信息（段落号、页码等）
        self._source_file: str = ""

    def add(self, chunks: list[str], embeddings: list[list[float]], metadata: list[dict] = None):
        """添加文档块及其向量"""
        self.chunks.extend(chunks)
        
        new_embeddings = np.array(embeddings)
        if self.embeddings.size == 0:
            self.embeddings = new_embeddings
        else:
            self.embeddings = np.vstack([self.embeddings, new_embeddings])
        
        if metadata:
            self.metadata.extend(metadata)
        else:
            self.metadata.extend([{"index": i} for i in range(len(chunks))])

    def search(self, query_embedding: list[float], top_k: int = 3) -> list[dict]:
        """
        向量检索：找到与 query 最相似的 top_k 个文档块。
        
        Returns:
            [{"chunk": "段落文本", "score": 0.85, "metadata": {...}}, ...]
        """
        if self.embeddings.size == 0:
            return []

        query_vec = np.array(query_embedding)
        
        # 计算 query 与所有 chunk 的余弦相似度
        scores = []
        for i in range(len(self.chunks)):
            score = cosine_similarity(query_vec, self.embeddings[i])
            scores.append(score)

        # 按相似度降序排列，取 Top-K
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            results.append({
                "chunk": self.chunks[idx],
                "score": round(scores[idx], 4),
                "metadata": self.metadata[idx] if idx < len(self.metadata) else {},
            })
        return results

    def save_cache(self, cache_path: str):
        """将向量缓存到 JSON 文件，避免重复调 Embedding API"""
        data = {
            "source_file": self._source_file,
            "chunks": self.chunks,
            "embeddings": self.embeddings.tolist(),
            "metadata": self.metadata,
        }
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    @classmethod
    def load_cache(cls, cache_path: str) -> Optional["VectorStore"]:
        """从缓存加载向量（如果存在）"""
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            store = cls()
            store.chunks = data["chunks"]
            store.embeddings = np.array(data["embeddings"])
            store.metadata = data.get("metadata", [])
            store._source_file = data.get("source_file", "")
            return store
        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def get_cache_path(file_path: str) -> str:
        """根据文件路径生成缓存文件路径"""
        file_hash = hashlib.md5(file_path.encode()).hexdigest()[:12]
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
        return os.path.join(cache_dir, f"rag_{file_hash}.json")

    def __len__(self):
        return len(self.chunks)
