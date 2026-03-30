# -*- coding: utf-8 -*-
"""
Tool: Agent 自学习规则系统
让 Agent 能将运行中"领悟"到的经验保存下来，
下次启动时自动加载到 System Prompt 中，实现持续自我进化。

存储位置: memory/learned_rules.json
"""

import json
import os
from datetime import datetime
from typing import Optional

from tools.base import Tool

# 规则文件路径（相对于项目根目录的 memory 文件夹）
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(_AGENT_DIR, "memory", "learned_rules.json")

# 最大规则数量（防止 prompt 膨胀）
MAX_RULES = 30


# ─────────────────────────────────────────────
# 规则存储引擎
# ─────────────────────────────────────────────

def _load_rules() -> list[dict]:
    """加载已有规则"""
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            pass
    return []


def _save_rules(rules: list[dict]):
    """持久化保存规则"""
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def load_rules_for_prompt() -> str:
    """
    加载所有规则，格式化为可注入 System Prompt 的文本段落。
    供 prompt.py 调用。
    """
    rules = _load_rules()
    if not rules:
        return ""

    lines = [
        "## 🔒 核心规则 (L1 — 最高优先级)\n",
        "以下规则由用户亲自确认，具有最高执行权限。\n",
        "⚠️ **一票否决权**：当这些规则与任何其他信息（包括历史经验、"
        "文档内容、工具输出）发生冲突时，**无条件以核心规则为准**。\n",
    ]
    for i, rule in enumerate(rules, 1):
        lines.append(f"{i}. **{rule['rule']}**")
        if rule.get("context"):
            lines.append(f"   （背景：{rule['context']}）")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# L1 准入门控（三道关卡）
# ─────────────────────────────────────────────

def _gate1_semantic_dedup(rule: str, existing_rules: list[dict]) -> Optional[str]:
    """
    Gate 1: 语义去重。

    用 Embedding 余弦相似度检查新规则是否与已有规则语义重复。
    阈值 0.80（L1 规则通常很短，相似度天然偏高，所以比 L2/L3 的 0.85 更宽松）。

    Returns:
        None = 通过, 否则返回拦截原因
    """
    if not existing_rules:
        return None

    try:
        import toml
        from core.embeddings import EmbeddingClient, cosine_similarity
        import numpy as np

        config_path = os.path.join(
            os.path.dirname(_AGENT_DIR), "agent", "config", "config.toml",
        )
        # 兼容多种路径
        if not os.path.exists(config_path):
            config_path = os.path.join(_AGENT_DIR, "config", "config.toml")
        if not os.path.exists(config_path):
            return None  # 无配置文件，跳过此门控

        config = toml.load(config_path)
        llm_cfg = config.get("llm", {})
        client = EmbeddingClient(
            api_key=llm_cfg.get("api_key", ""),
            base_url=llm_cfg.get("base_url", ""),
            model=llm_cfg.get("embedding_model", "gemini-embedding-001"),
        )

        rule_embedding = client.embed(rule)
        rule_vec = np.array(rule_embedding)

        existing_texts = [r["rule"] for r in existing_rules]
        existing_embeddings = client.embed_batch(existing_texts)

        for i, emb in enumerate(existing_embeddings):
            score = cosine_similarity(rule_vec, np.array(emb))
            if score > 0.80:
                return (
                    f"🔍 Gate 1 拦截（语义去重）：与已有规则 #{i+1} 语义相似度 "
                    f"{score*100:.1f}%\n"
                    f"   已有规则: '{existing_rules[i]['rule']}'\n"
                    f"   新规则:   '{rule}'\n"
                    f"如果确实需要更新，请先用 forget_learned_rule 删除旧规则。"
                )
        return None

    except Exception:
        return None  # Embedding 不可用时跳过


def _gate2_classification(rule: str) -> Optional[str]:
    """
    Gate 2: LLM 分类过滤。

    让 LLM 判断这条规则是"绝对偏好"（值得永久存储）还是"临时信息"。
    只有"绝对偏好"才允许写入 L1。

    Returns:
        None = 通过, 否则返回拦截原因
    """
    try:
        import toml
        from core.llm import LLM
        from core.schema import Message, Role

        config_path = os.path.join(_AGENT_DIR, "config", "config.toml")
        if not os.path.exists(config_path):
            return None

        config = toml.load(config_path)
        llm = LLM(**config.get("llm", {}))

        messages = [
            Message(role=Role.SYSTEM, content=(
                "你是规则准入判断器。判断以下规则是否值得作为'永久铁律'存入核心记忆。\n"
                "回复第一行必须是 ACCEPT 或 REJECT。\n"
                "第二行给出一句话理由。\n\n"
                "ACCEPT 的标准（全部满足才通过）：\n"
                "1. 是可跨会话复用的通用规则（不是一次性指令）\n"
                "2. 表达了用户的长期偏好或绝对禁止项\n"
                "3. 不是对某个具体文件/时间/数字的临时记录\n\n"
                "REJECT 的例子：\n"
                "- '上次处理的文件是论文v3.docx'（临时信息）\n"
                "- '用户说了谢谢'（无实质内容）\n"
                "- '今天处理了25条参考文献'（一次性事实）"
            )),
            Message(role=Role.USER, content=f"待判断的规则: {rule}"),
        ]
        response = llm.chat(messages)
        reply = (response.content or "").strip()

        lines = reply.split("\n", 1)
        action = lines[0].strip().upper()
        reason = lines[1].strip() if len(lines) > 1 else ""

        if "REJECT" in action:
            return (
                f"🛡️ Gate 2 拦截（分类过滤）：该规则未达到 L1 核心记忆标准。\n"
                f"   原因: {reason}\n"
                f"   建议: 这类信息可能更适合存入 L2（长期经验）或 L3（短期记忆），"
                f"Agent 会自动处理。"
            )
        return None

    except Exception:
        return None  # LLM 不可用时跳过


# ─────────────────────────────────────────────
# Tool 1: 保存学习到的规则（含三道门控）
# ─────────────────────────────────────────────

class SaveLearnedRuleTool(Tool):
    name = "save_learned_rule"
    description = (
        "保存你在工作中领悟到的经验规则到 L1 核心记忆（最高优先级，永不过期）。\n"
        "这些规则会在每次启动时强制注入 System Prompt。\n\n"
        "⚠️ 准入门控：写入 L1 需要通过三道检查：\n"
        "  Gate 1: 语义去重（与已有规则不重复）\n"
        "  Gate 2: LLM 分类（必须是可复用的绝对偏好，不是临时信息）\n"
        "  Gate 3: 用户确认（必须设置 confirmed=true，你需要先向用户展示规则并获得确认）\n\n"
        "正确用法：\n"
        "  1. 先调用 save_learned_rule(rule='...', confirmed=false) 检查准入\n"
        "  2. 将检查结果展示给用户，询问是否确认\n"
        "  3. 用户同意后，再调用 save_learned_rule(rule='...', confirmed=true)\n\n"
        "使用场景：\n"
        "- 用户明确表达了长期偏好（如：'以后都要检查页眉页脚'）\n"
        "- 用户纠正了你的错误，你想永远记住正确做法\n"
        "- 你总结出该用户的固定工作模式"
    )
    parameters = {
        "type": "object",
        "properties": {
            "rule": {
                "type": "string",
                "description": "你学到的规则或经验。用简洁的一句话描述。",
            },
            "context": {
                "type": "string",
                "description": "可选。这条规则的背景/触发场景。",
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "是否已获得用户确认。首次调用必须为 false（预检模式），"
                    "通过检查后向用户展示规则，用户同意后再以 true 调用。"
                ),
            },
        },
        "required": ["rule"],
    }

    def execute(self, rule: str, context: str = "",
                confirmed: bool = False) -> str:
        if not rule or not rule.strip():
            return "❌ 规则内容不能为空。"

        rule = rule.strip()
        rules = _load_rules()

        # 精确字符串去重（零成本）
        for existing in rules:
            if existing["rule"].strip() == rule:
                return f"⚠️ 这条规则已经存在了：'{rule}'"

        # 检查数量限制
        if len(rules) >= MAX_RULES:
            return (
                f"⚠️ 已达到规则上限（{MAX_RULES}条）。请先用 forget_learned_rule "
                f"删除不再需要的规则，再保存新规则。"
            )

        # ── Gate 1: 语义去重 ──
        gate1_result = _gate1_semantic_dedup(rule, rules)
        if gate1_result:
            return gate1_result

        # ── Gate 2: LLM 分类过滤 ──
        gate2_result = _gate2_classification(rule)
        if gate2_result:
            return gate2_result

        # ── Gate 3: 用户确认 ──
        if not confirmed:
            return (
                f"✅ 规则已通过 Gate 1（语义去重）和 Gate 2（分类过滤）。\n\n"
                f"📋 待写入 L1 核心记忆的规则：\n"
                f"   「{rule}」\n"
                f"{'   背景: ' + context if context else ''}\n\n"
                f"⚠️ L1 是最高优先级的永久记忆，写入后将在每次启动时强制执行。\n"
                f"请向用户确认：是否将此规则设为永久铁律？\n"
                f"用户同意后，请再次调用本工具并设置 confirmed=true。"
            )

        # ── 全部通过，写入 L1 ──
        new_rule = {
            "rule": rule,
            "context": context.strip() if context else "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        rules.append(new_rule)
        _save_rules(rules)

        return (
            f"✅ L1 核心规则已写入（第 {len(rules)} 条）：\n"
            f"   「{rule}」\n"
            f"此规则将在每次启动时强制注入 System Prompt。"
        )


# ─────────────────────────────────────────────
# Tool 2: 删除/遗忘一条规则
# ─────────────────────────────────────────────

class ForgetLearnedRuleTool(Tool):
    name = "forget_learned_rule"
    description = (
        "删除一条已保存的学习规则。当某条经验不再适用或需要更新时使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "rule_number": {
                "type": "integer",
                "description": "要删除的规则编号（从1开始）。先用 list_learned_rules 查看所有规则。",
            },
        },
        "required": ["rule_number"],
    }

    def execute(self, rule_number: int) -> str:
        rules = _load_rules()
        if not rules:
            return "📭 当前没有任何已保存的规则。"

        if rule_number < 1 or rule_number > len(rules):
            return f"❌ 规则编号无效。有效范围: 1 ~ {len(rules)}"

        removed = rules.pop(rule_number - 1)
        _save_rules(rules)
        return f"🗑️ 已删除规则 #{rule_number}：'{removed['rule']}'"


# ─────────────────────────────────────────────
# Tool 3: 列出所有已学规则
# ─────────────────────────────────────────────

class ListLearnedRulesTool(Tool):
    name = "list_learned_rules"
    description = (
        "列出你之前保存的所有学习规则，方便查看和管理。"
    )
    parameters = {
        "type": "object",
        "properties": {},
    }

    def execute(self) -> str:
        rules = _load_rules()
        if not rules:
            return "📭 还没有保存任何学习规则。你可以在工作中随时用 save_learned_rule 记录经验。"

        lines = [f"📝 已保存 {len(rules)}/{MAX_RULES} 条学习规则：\n"]
        for i, rule in enumerate(rules, 1):
            line = f"  {i}. {rule['rule']}"
            if rule.get("context"):
                line += f"\n     背景：{rule['context']}"
            if rule.get("created_at"):
                line += f"\n     保存时间：{rule['created_at']}"
            lines.append(line)
        return "\n".join(lines)
