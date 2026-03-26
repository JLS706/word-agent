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
import math
from datetime import datetime
from typing import Optional

from core.logger import logger


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
        将一轮对话存入向量记忆（含矛盾消解）。

        改进：存入前检测语义冲突 —— 如果新记忆与某条旧记忆
        相似度 > 0.85，说明是同一话题的更新，用新记忆替换旧的，
        避免"喜欢苹果"和"不喜欢苹果"同时存在。
        """
        if not self.embed_client or self._vector_store is None:
            return

        summary = f"用户: {user_input[:200]}\nAgent: {agent_reply[:300]}"

        try:
            embedding = self.embed_client.embed(summary)

            # ── 矛盾消解：检测是否与旧记忆冲突 ──
            if len(self._vector_store) > 0:
                from core.embeddings import cosine_similarity
                import numpy as np
                query_vec = np.array(embedding)
                for i in range(len(self._vector_store.chunks)):
                    score = cosine_similarity(
                        query_vec, self._vector_store.embeddings[i]
                    )
                    if score > 0.85:  # 语义高度重合 → 替换旧记忆
                        logger.debug(
                            "  [Memory] 检测到语义冲突(%.1f%%)，替换旧记忆",
                            score * 100,
                        )
                        self._vector_store.chunks[i] = summary
                        self._vector_store.embeddings[i] = query_vec
                        self._vector_store.metadata[i] = {
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M")
                        }
                        self._vector_store.save_cache(self._vector_cache_path)
                        return

            # 无冲突 → 正常追加
            metadata = [{"time": datetime.now().strftime("%Y-%m-%d %H:%M")}]
            self._vector_store.add([summary], [embedding], metadata)
            self._vector_store.save_cache(self._vector_cache_path)

            # ── 反思压缩：记忆太多时触发归纳 ──
            if len(self._vector_store) > 100:
                self._reflect_and_compress()

        except Exception as e:
            logger.warning("  [Memory] 向量存储失败(不影响主功能): %s", e)

    def recall_relevant(self, query: str, top_k: int = 3,
                        min_score: float = 0.45) -> str:
        """
        根据当前问题，召回最相关的历史对话片段（含时间衰减）。

        改进：最终得分 = 0.7 × 语义相似度 + 0.3 × 时间新鲜度
        时间新鲜度使用指数衰减：recency = exp(-days / 30)
        即 1 天前≈0.97，7天前≈0.79，30 天前≈0.37
        """
        if (not self.embed_client or self._vector_store is None
                or len(self._vector_store) == 0):
            return ""

        try:
            query_embedding = self.embed_client.embed(query)
            # 取更多候选用于重评分
            candidates = self._vector_store.search(
                query_embedding, top_k=top_k * 3
            )

            if not candidates:
                return ""

            # ── 时间衰减重评分 ──
            now = datetime.now()
            scored = []
            for r in candidates:
                semantic = r["score"]
                # 计算时间新鲜度
                recency = 0.5  # 默认值（无时间信息时）
                time_str = r.get("metadata", {}).get("time", "")
                if time_str:
                    try:
                        mem_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                        days_ago = (now - mem_time).total_seconds() / 86400
                        recency = math.exp(-days_ago / 30)  # 30天半衰期
                    except ValueError:
                        pass

                final_score = 0.7 * semantic + 0.3 * recency
                scored.append({**r, "final_score": final_score})

            # 按混合得分排序，取 top_k
            scored.sort(key=lambda x: x["final_score"], reverse=True)
            relevant = [
                r for r in scored[:top_k]
                if r["final_score"] >= min_score
            ]

            if not relevant:
                return ""

            parts = []
            for i, r in enumerate(relevant, 1):
                score_pct = round(r["final_score"] * 100, 1)
                parts.append(f"[相关度 {score_pct}%] {r['chunk']}")

            return "\n\n".join(parts)
        except Exception as e:
            logger.warning("  [Memory] 向量召回失败(不影响主功能): %s", e)
            return ""

    # ─────────────────────────────────────────────
    # 反思压缩（Reflection）
    # ─────────────────────────────────────────────

    def _reflect_and_compress(self):
        """
        当向量记忆超过阈值时，压缩旧记忆为高层级认知。

        机制（受 Generative Agents / Smallville 启发）：
          1. 取最旧的 50 条记忆
          2. 让 LLM 归纳为 ~5 条长期认知
          3. 用归纳结果替换原始 50 条 → 记忆数量从 100+ 缩减
        """
        if self._vector_store is None or len(self._vector_store) <= 100:
            return

        logger.info("  [Memory] 反思压缩：%d 条记忆超过阈值，归纳旧记忆...",
                    len(self._vector_store))

        # 取最旧的 50 条
        old_texts = self._vector_store.chunks[:50]
        combined = "\n".join(f"- {t}" for t in old_texts)

        try:
            import toml
            from core.llm import LLM
            from core.schema import Message, Role

            config_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "config", "config.toml",
            )
            config = toml.load(config_path)
            llm = LLM(**config.get("llm", {}))

            messages = [
                Message(role=Role.SYSTEM, content=(
                    "你是一个记忆管理助手。以下是用户与 AI 助手的多轮历史对话摘要。"
                    "请将它们归纳为 5 条简洁的长期记忆，保留关键偏好、"
                    "常用操作模式和重要结论。每条一行，用 '- ' 开头。"
                )),
                Message(role=Role.USER, content=f"历史对话：\n{combined}"),
            ]
            response = llm.chat(messages)
            insights = response.content or ""

            # 解析归纳结果
            insight_lines = [
                line.strip().lstrip("- ").strip()
                for line in insights.split("\n")
                if line.strip().startswith("- ")
            ]
            if not insight_lines:
                return  # 归纳失败，不动原始数据

            # 为归纳结果生成 Embedding
            new_embeddings = self.embed_client.embed_batch(insight_lines)
            new_metadata = [
                {"time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "type": "reflection"}
                for _ in insight_lines
            ]

            # 删除旧的 50 条，插入归纳结果
            import numpy as np
            self._vector_store.chunks = (
                insight_lines + self._vector_store.chunks[50:]
            )
            self._vector_store.embeddings = np.vstack([
                np.array(new_embeddings),
                self._vector_store.embeddings[50:],
            ])
            self._vector_store.metadata = (
                new_metadata + self._vector_store.metadata[50:]
            )
            self._vector_store.save_cache(self._vector_cache_path)

            logger.info(
                "  [Memory] 反思完成：50 条旧记忆 → %d 条长期认知",
                len(insight_lines),
            )
        except Exception as e:
            logger.warning("  [Memory] 反思压缩失败(不影响主功能): %s", e)

