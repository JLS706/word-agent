# -*- coding: utf-8 -*-
"""
DocMaster Agent - 本地记忆系统
将用户的操作历史和偏好保存到本地 JSON 文件中，实现跨会话记忆。

v2: 新增向量记忆（RAG 式对话召回）
  - 每轮对话结束后，将 Q+A 摘要存入向量库
  - 新对话开始时，用用户问题作为 query 检索最相关的历史片段
  - 复用 core/embeddings.py 的 VectorStore，无需外部向量数据库
"""

import json
import os
from datetime import datetime
from typing import Optional


class Memory:
    """本地记忆管理器（含向量召回）"""

    def __init__(self, memory_dir: str = "memory", embed_client=None):
        self.memory_dir = os.path.abspath(memory_dir)
        self.history_file = os.path.join(self.memory_dir, "history.json")
        self._data = self._load()

        # ── 向量记忆 ──
        self.embed_client = embed_client
        self._vector_store = None          # 延迟初始化
        self._vector_cache_path = os.path.join(self.memory_dir, "memory_vectors.json")
        self._load_vector_store()

    def _load_vector_store(self):
        """从缓存加载向量记忆（如果存在）"""
        if self.embed_client is None:
            return
        try:
            from core.embeddings import VectorStore
            cached = VectorStore.load_cache(self._vector_cache_path)
            if cached:
                self._vector_store = cached
            else:
                self._vector_store = VectorStore()
        except Exception:
            from core.embeddings import VectorStore
            self._vector_store = VectorStore()

    def _load(self) -> dict:
        """从 JSON 文件加载记忆"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"sessions": [], "preferences": {}}

    def _save(self):
        """持久化保存到 JSON 文件"""
        os.makedirs(self.memory_dir, exist_ok=True)
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def add_session(self, file_path: str, actions: list[str], summary: str):
        """记录一次操作会话"""
        session = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "file": os.path.abspath(file_path),
            "actions": actions,
            "summary": summary,
        }
        self._data["sessions"].append(session)
        # 只保留最近 50 条记录
        if len(self._data["sessions"]) > 50:
            self._data["sessions"] = self._data["sessions"][-50:]
        self._save()

    def set_preference(self, key: str, value):
        """保存用户偏好"""
        self._data["preferences"][key] = value
        self._save()

    def get_preference(self, key: str, default=None):
        """读取用户偏好"""
        return self._data["preferences"].get(key, default)

    def get_recent_sessions(self, n: int = 5) -> list[dict]:
        """获取最近 N 条操作记录"""
        return self._data["sessions"][-n:]

    def get_last_file(self) -> Optional[str]:
        """获取上次处理的文件路径"""
        sessions = self._data["sessions"]
        if sessions:
            return sessions[-1].get("file")
        return None

    def get_context_summary(self, recalled_context: str = "") -> str:
        """生成记忆摘要，注入到 System Prompt 中"""
        recent = self.get_recent_sessions(3)
        if not recent and not recalled_context:
            return ""

        lines = []

        # 向量召回的相关历史（RAG 式）
        if recalled_context:
            lines.append("📌 与当前问题最相关的历史对话：\n")
            lines.append(recalled_context)
            lines.append("")

        # 最近操作记录（原有逻辑）
        if recent:
            lines.append("最近操作记录：")
            for s in recent:
                actions_str = ", ".join(s["actions"]) if s["actions"] else "无"
                lines.append(
                    f"- [{s['time']}] 文件: {os.path.basename(s['file'])} | "
                    f"操作: {actions_str} | {s['summary']}"
                )

        last_file = self.get_last_file()
        pref_lines = []
        if last_file:
            pref_lines.append(f"- 上次处理的文件: {last_file}")
        for k, v in self._data["preferences"].items():
            pref_lines.append(f"- {k}: {v}")

        result = "\n".join(lines)
        if pref_lines:
            result += "\n\n用户偏好：\n" + "\n".join(pref_lines)
        return result

    # ─────────────────────────────────────────────
    # 向量记忆（RAG 式对话召回）
    # ─────────────────────────────────────────────

    def add_to_vector(self, user_input: str, agent_reply: str):
        """
        将一轮对话存入向量记忆。

        原理与 RAG 完全一样：
          文本 → Embedding → 存入 VectorStore → JSON 持久化

        只是数据源从"文档段落"变成了"对话摘要"。
        """
        if not self.embed_client or self._vector_store is None:
            return

        # 拼接对话摘要（Q+A 一起存，检索时语义更完整）
        summary = f"用户: {user_input[:200]}\nAgent: {agent_reply[:300]}"

        try:
            embedding = self.embed_client.embed(summary)
            metadata = [{"time": datetime.now().strftime("%Y-%m-%d %H:%M")}]
            self._vector_store.add([summary], [embedding], metadata)

            # 持久化到 JSON（和 RAG 用同一个缓存机制）
            self._vector_store.save_cache(self._vector_cache_path)
        except Exception as e:
            # 向量存储失败不影响主流程
            print(f"  [Memory] 向量存储失败(不影响主功能): {e}")

    def recall_relevant(self, query: str, top_k: int = 3,
                        min_score: float = 0.45) -> str:
        """
        根据当前问题，召回最相关的历史对话片段。

        原理与 RAG 的 search_document 完全一样：
          query → Embedding → 余弦相似度 → Top-K

        Args:
            query: 用户当前的问题
            top_k: 最多返回几条历史
            min_score: 最低相似度阈值（过滤噪音）

        Returns:
            格式化的历史片段文本，可直接注入 System Prompt
        """
        if (not self.embed_client or self._vector_store is None
                or len(self._vector_store) == 0):
            return ""

        try:
            query_embedding = self.embed_client.embed(query)
            results = self._vector_store.search(query_embedding, top_k=top_k)

            # 过滤低相似度结果
            relevant = [r for r in results if r["score"] >= min_score]
            if not relevant:
                return ""

            parts = []
            for i, r in enumerate(relevant, 1):
                score_pct = round(r["score"] * 100, 1)
                parts.append(f"[相关度 {score_pct}%] {r['chunk']}")

            return "\n\n".join(parts)
        except Exception as e:
            print(f"  [Memory] 向量召回失败(不影响主功能): {e}")
            return ""

