# -*- coding: utf-8 -*-
"""
DocMaster Agent - Skills 管理器

实现 Skill 插件化架构：
  1. 扫描 skills/ 目录，加载所有 .md 文件
  2. 解析 YAML frontmatter（名称、关键词、工具列表、config 参数块）
  3. 根据用户输入，用 Embedding 余弦相似度匹配最相关的 Skill
  4. 返回匹配到的 Skill 内容，注入到 System Prompt
  5. 合并多个 Skill 的 config 块，注入到工具参数（高优先级覆盖低优先级）

这样做的好处：
  - 用户说"你好"时不加载任何 Skill（省 Token）
  - 新增业务只需加一个 .md 文件（不改代码）
  - 换规范只需换一个 .md 文件中的 config 块（零代码变更）
  - 匹配逻辑复用 RAG 模块的 Embedding + 余弦相似度
"""

import os
import re
from copy import deepcopy
from typing import Any, Optional

import yaml

from core.logger import logger


class Skill:
    """一个 Skill 的结构化表示"""

    def __init__(self, name: str, description: str, keywords: list[str],
                 tools: list[str], priority: int, content: str, file_path: str,
                 config: dict | None = None):
        self.name = name
        self.description = description
        self.keywords = keywords
        self.tools = tools
        self.priority = priority
        self.content = content          # Markdown 正文（不含 frontmatter）
        self.file_path = file_path
        self.config = config or {}      # Skill 参数块（如 format_rules）
        self.embedding: list[float] = []  # 启动时预计算

    def get_search_text(self) -> str:
        """用于生成 Embedding 的文本（名称 + 描述 + 关键词）"""
        return f"{self.name} {self.description} {' '.join(self.keywords)}"

    def __repr__(self):
        return f"Skill({self.name}, keywords={self.keywords[:3]}...)"


class SkillManager:
    """
    Skill 管理器 — 扫描、加载、匹配 Skills。
    
    匹配策略（双层）：
      Layer 1: 关键词快速匹配（零 API 调用）
      Layer 2: Embedding 语义匹配（更智能，1 次 API 调用）
    
    优先用关键词匹配，没命中时再用 Embedding。
    """

    def __init__(self, skills_dir: str, embed_client=None):
        """
        Args:
            skills_dir: skills/ 目录的绝对路径
            embed_client: EmbeddingClient 实例（可选，用于语义匹配）
        """
        self.skills_dir = skills_dir
        self.embed_client = embed_client
        self.skills: list[Skill] = []
        self._embeddings_ready = False

        # 自动加载
        self._load_all()

    def _load_all(self):
        """扫描 skills/ 目录，加载所有 .md 文件"""
        if not os.path.isdir(self.skills_dir):
            return

        for fname in sorted(os.listdir(self.skills_dir)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(self.skills_dir, fname)
            skill = self._parse_skill_file(fpath)
            if skill:
                self.skills.append(skill)

    def _parse_skill_file(self, file_path: str) -> Optional[Skill]:
        """
        解析 Skill 的 Markdown 文件。
        
        格式：
        ---
        name: xxx
        description: xxx
        trigger_keywords: [a, b, c]
        tools: [tool1, tool2]
        priority: 10
        config:
          format_rules:
            正文:
              font_cn: "宋体"
              font_size: 12.0
        ---
        正文内容...
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return None

        # 提取 YAML frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
        if not fm_match:
            return None

        frontmatter_text = fm_match.group(1)
        content = text[fm_match.end():]

        # 使用 PyYAML 解析 frontmatter（支持嵌套结构）
        try:
            meta = yaml.safe_load(frontmatter_text)
            if not isinstance(meta, dict):
                return None
        except yaml.YAMLError as e:
            logger.warning("[Skills] YAML 解析失败 (%s): %s", file_path, e)
            return None

        name = meta.get("name", "")
        if not name:
            return None

        return Skill(
            name=name,
            description=meta.get("description", ""),
            keywords=meta.get("trigger_keywords", []),
            tools=meta.get("tools", []),
            priority=int(meta.get("priority", 5)),
            content=content.strip(),
            file_path=file_path,
            config=meta.get("config", {}),
        )

    # ─────────────────────────────────────────
    # 匹配逻辑
    # ─────────────────────────────────────────

    def match(self, user_input: str, threshold: float = 0.5, max_results: int = 2) -> list[Skill]:
        """
        根据用户输入匹配相关 Skills。
        
        策略：
          1. 先用关键词匹配（快速、免费）
          2. 没命中时，用 Embedding 语义匹配（智能、便宜）
        
        Args:
            user_input: 用户的自然语言输入
            threshold: Embedding 匹配的最低相似度阈值
        
        Returns:
            匹配到的 Skill 列表（按优先级排序，高优先级在前）
        """
        # Layer 1: 关键词匹配
        keyword_matches = self._match_by_keywords(user_input)
        if keyword_matches:
            return keyword_matches

        # Layer 2: Embedding 语义匹配
        if self.embed_client:
            return self._match_by_embedding(user_input, threshold, max_results)

        return []

    def _match_by_keywords(self, user_input: str) -> list[Skill]:
        """Layer 1: 关键词匹配（零 API 调用）"""
        input_lower = user_input.lower()
        matched = []

        for skill in self.skills:
            hit_count = sum(1 for kw in skill.keywords if kw.lower() in input_lower)
            if hit_count > 0:
                matched.append((skill, hit_count))

        if not matched:
            return []

        # 按命中关键词数 × 优先级排序（高优先级在前）
        matched.sort(key=lambda x: (x[1] * x[0].priority), reverse=True)
        return [s for s, _ in matched]

    def _match_by_embedding(self, user_input: str, threshold: float, max_results: int = 2) -> list[Skill]:
        """Layer 2: Embedding 语义匹配（1 次 API 调用）"""
        # 确保 Skill Embeddings 已预计算
        self._ensure_embeddings()

        if not self._embeddings_ready:
            return []

        try:
            import numpy as np
            from core.embeddings import cosine_similarity

            query_vec = np.array(self.embed_client.embed(user_input))

            scored = []
            for skill in self.skills:
                if skill.embedding:
                    score = cosine_similarity(query_vec, np.array(skill.embedding))
                    if score >= threshold:
                        scored.append((skill, score))

            scored.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in scored[:max_results]]
        except Exception:
            return []

    def _ensure_embeddings(self):
        """预计算所有 Skill 的 Embedding（只在首次匹配时执行）"""
        if self._embeddings_ready or not self.embed_client:
            return

        try:
            texts = [s.get_search_text() for s in self.skills]
            if not texts:
                return
            embeddings = self.embed_client.embed_batch(texts)
            for skill, emb in zip(self.skills, embeddings):
                skill.embedding = emb
            self._embeddings_ready = True
        except Exception as e:
            logger.warning("[Skills] Embedding 预计算失败: %s", e)

    # ─────────────────────────────────────────
    # Config 合并（核心新增功能）
    # ─────────────────────────────────────────

    def get_active_config(self, matched_skills: list[Skill]) -> dict:
        """
        合并多个 Skill 的 config 块，返回最终生效的配置。

        合并策略：高优先级覆盖，低优先级填充。
          - matched_skills 已按优先级降序排列（高优先级在前）
          - 先铺低优先级的 config（兜底）
          - 再用高优先级的 config 覆盖（特化）
          - 值为 None/null 表示"不要这个字段"（显式跳过）

        示例：
          通用 Skill:    { format_rules: { 正文: { font_cn: "宋体", line_spacing: 1.5 } } }
          B 校 Skill:    { format_rules: { 正文: { font_cn: "仿宋" } } }
          合并结果:      { format_rules: { 正文: { font_cn: "仿宋", line_spacing: 1.5 } } }

        Returns:
            合并后的 config 字典
        """
        if not matched_skills:
            return {}

        # 过滤出有 config 的 Skill
        skills_with_config = [s for s in matched_skills if s.config]
        if not skills_with_config:
            return {}

        # 按优先级升序排列（低优先级先铺底，高优先级后覆盖）
        # matched_skills 已经是降序的，反转即可
        ordered = list(reversed(skills_with_config))

        # 逐层深度合并
        merged = {}
        for skill in ordered:
            merged = self._deep_merge(merged, skill.config)

        return merged

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """
        深度合并两个字典。

        规则：
          - override 中的值覆盖 base 中的同名键
          - 如果两边都是 dict → 递归合并
          - 如果 override 的值是 None → 标记为显式跳过（保留 None）
          - 否则 override 的值直接覆盖 base
        """
        result = deepcopy(base)
        for key, value in override.items():
            if (key in result
                    and isinstance(result[key], dict)
                    and isinstance(value, dict)):
                result[key] = SkillManager._deep_merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result

    # ─────────────────────────────────────────
    # 输出
    # ─────────────────────────────────────────

    def build_skills_context(self, matched_skills: list[Skill]) -> str:
        """将匹配到的 Skills 格式化为 Prompt 注入文本"""
        if not matched_skills:
            return ""

        parts = ["## 已加载的技能手册\n"]
        for skill in matched_skills:
            parts.append(f"### {skill.name}\n")
            parts.append(skill.content)
            parts.append("")

        return "\n".join(parts)

    def list_skills(self) -> list[dict]:
        """列出所有已加载的 Skills"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "keywords": s.keywords,
                "tools": s.tools,
                "has_config": bool(s.config),
            }
            for s in self.skills
        ]
