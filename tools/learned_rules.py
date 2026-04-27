# -*- coding: utf-8 -*-
"""
Tool: Agent 自学习规则系统 + L1 用户画像

v1: learned_rules.json — 逐条铁律（旧版，保留向后兼容）
v2: user_profile.md    — Markdown 用户画像（新版，LLM 增量更新）

旧版工具 (SaveLearnedRuleTool / ForgetLearnedRuleTool / ListLearnedRulesTool) 保留兼容。
新版工具 (UpdateProfileTool / ViewProfileTool) 用于管理 .md 画像。

首次启动时，如果存在旧 learned_rules.json 但不存在 user_profile.md，
会自动将旧规则迁移到画像的 ## 铁律 section。

存储位置:
  - memory/learned_rules.json  (旧版，向后兼容)
  - memory/user_profile.md     (新版，L1 用户画像)
"""

import json
import os
import re
from datetime import datetime
from typing import Optional

from tools.base import Tool

# 规则文件路径（相对于项目根目录的 memory 文件夹）
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(_AGENT_DIR, "memory", "learned_rules.json")
PROFILE_FILE = os.path.join(_AGENT_DIR, "memory", "user_profile.md")

# 画像字数上限（防膨胀）
PROFILE_MAX_CHARS = 1500

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


# ═════════════════════════════════════════════
# L1 用户画像引擎 (v2)
# ═════════════════════════════════════════════

_DEFAULT_PROFILE = """\
# 用户画像

## 基本信息
- （Agent 会在交互过程中自动补充）

## 写作偏好
- （尚未记录）

## 铁律（绝对禁止项）
- 处理完文档后必须关闭 Word 进程
- 不得删除用户未明确要求删除的内容

## 历史踩坑
- （尚未记录）
"""


def load_profile() -> str:
    """加载用户画像。不存在时自动创建默认模板（含旧规则迁移）。"""
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except IOError:
            pass

    # 首次启动：尝试从旧 learned_rules.json 迁移
    profile = _migrate_from_rules()
    save_profile(profile)
    return profile


def save_profile(content: str):
    """持久化保存画像到 .md 文件。"""
    os.makedirs(os.path.dirname(PROFILE_FILE), exist_ok=True)
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        f.write(content)


def _migrate_from_rules() -> str:
    """将旧 learned_rules.json 迁移为画像的 ## 铁律 section。"""
    rules = _load_rules()
    if not rules:
        return _DEFAULT_PROFILE

    rule_lines = "\n".join(f"- {r['rule']}" for r in rules)
    profile = _DEFAULT_PROFILE

    # 替换默认铁律 section
    old_section = (
        "## 铁律（绝对禁止项）\n"
        "- 处理完文档后必须关闭 Word 进程\n"
        "- 不得删除用户未明确要求删除的内容"
    )
    new_section = f"## 铁律（绝对禁止项）\n{rule_lines}"
    profile = profile.replace(old_section, new_section)
    return profile


def load_profile_for_prompt() -> str:
    """
    加载画像，格式化为可注入 System Prompt 的文本段落。
    供 prompt.py 调用（替代 load_rules_for_prompt）。
    """
    profile = load_profile()
    if not profile or not profile.strip():
        return ""

    return (
        "## 🧠 用户画像 (L1 — 最高优先级)\n\n"
        "以下是你对这位用户的了解。请据此提供个性化服务。\n"
        "⚠️ 其中「铁律」部分具有一票否决权，与任何其他信息冲突时无条件以铁律为准。\n\n"
        f"{profile.strip()}"
    )


def extract_taboos_from_profile(profile: str = "") -> list[str]:
    """
    从画像中提取 ## 铁律 section 的条目（供三明治注入和后校验使用）。

    Returns:
        铁律文本列表，如 ["处理完文档后必须关闭 Word 进程", ...]
    """
    if not profile:
        profile = load_profile()

    match = re.search(
        r"##\s*铁律[^\n]*\n(.*?)(?=\n##|\Z)",
        profile,
        re.DOTALL,
    )
    if not match:
        return []

    lines = match.group(1).strip().split("\n")
    taboos = []
    for line in lines:
        line = line.strip()
        if line.startswith("- "):
            taboos.append(line[2:].strip())
        elif line.startswith("* "):
            taboos.append(line[2:].strip())
        elif line and not line.startswith("#"):
            taboos.append(line)
    return [t for t in taboos if t]


# ─────────────────────────────────────────────
# Tool 4: 更新用户画像（LLM 增量重写）
# ─────────────────────────────────────────────

class UpdateProfileTool(Tool):
    name = "update_profile"
    description = (
        "更新 L1 用户画像。当你在交互中发现了用户的新偏好、写作习惯、导师要求、"
        "或需要新增/修改铁律时，调用此工具。\n\n"
        "你需要提供一段新发现的信息（new_info），工具会让 LLM 读取旧画像并增量更新。\n"
        "画像有字数上限（1500字），LLM 会自动精炼冗余内容。\n\n"
        "使用场景：\n"
        "- 用户透露了身份/导师/研究方向等信息\n"
        "- 用户表达了长期写作偏好（字体、格式、引用风格）\n"
        "- 需要新增或修改铁律（绝对禁止项）\n"
        "- 用户纠正了你的错误，需要记住正确做法"
    )
    parameters = {
        "type": "object",
        "properties": {
            "new_info": {
                "type": "string",
                "description": "新发现的用户信息或偏好，用自然语言描述。",
            },
        },
        "required": ["new_info"],
    }

    def execute(self, new_info: str) -> str:
        if not new_info or not new_info.strip():
            return "❌ 新信息不能为空。"

        old_profile = load_profile()

        try:
            import toml
            from core.llm import LLM
            from core.schema import Message, Role

            config_path = os.path.join(_AGENT_DIR, "config", "config.toml")
            if not os.path.exists(config_path):
                return "❌ 配置文件不存在，无法调用 LLM 更新画像。"

            config = toml.load(config_path)
            llm = LLM(**config.get("llm", {}))

            messages = [
                Message(role=Role.SYSTEM, content=(
                    "你是用户画像更新器。给定旧画像和一段新信息，请输出更新后的完整画像。\n\n"
                    "规则：\n"
                    "1. 保持 Markdown 格式，保留所有 ## 章节标题\n"
                    "2. 将新信息融入合适的章节（基本信息/写作偏好/铁律/历史踩坑）\n"
                    "3. 如果新信息与旧内容矛盾，以新信息为准\n"
                    "4. 如果新信息是补充，追加到相应章节\n"
                    f"5. 总字数不超过 {PROFILE_MAX_CHARS} 字，必要时精炼冗余内容\n"
                    "6. 不要添加任何解释说明，只输出更新后的画像 Markdown 全文\n"
                    "7. 「## 铁律」章节中的条目必须用 - 开头的列表格式"
                )),
                Message(role=Role.USER, content=(
                    f"旧画像：\n```markdown\n{old_profile}\n```\n\n"
                    f"新信息：{new_info.strip()}"
                )),
            ]
            response = llm.chat(messages)
            updated = (response.content or "").strip()

            # 清理 LLM 可能包裹的 ```markdown ... ```
            if updated.startswith("```"):
                lines = updated.split("\n")
                # 去掉首行 ```markdown 和末行 ```
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                updated = "\n".join(lines).strip()

            if not updated:
                return "❌ LLM 返回了空内容，画像未更新。"

            # 防膨胀检查
            if len(updated) > PROFILE_MAX_CHARS * 1.5:
                return (
                    f"⚠️ 更新后画像超过字数上限（{len(updated)} > {PROFILE_MAX_CHARS}），"
                    "未保存。请简化新信息后重试。"
                )

            save_profile(updated)
            return (
                f"✅ 用户画像已更新（{len(updated)} 字）。\n"
                f"画像将在下次对话时自动注入 System Prompt。"
            )

        except Exception as e:
            return f"❌ 画像更新失败: {e}"


# ─────────────────────────────────────────────
# Tool 5: 查看用户画像
# ─────────────────────────────────────────────

class ViewProfileTool(Tool):
    name = "view_profile"
    description = (
        "查看当前的 L1 用户画像，了解你对这位用户的所有记录。"
    )
    parameters = {
        "type": "object",
        "properties": {},
    }

    def execute(self) -> str:
        profile = load_profile()
        if not profile or not profile.strip():
            return "📭 用户画像为空。"

        char_count = len(profile)
        return (
            f"📋 当前用户画像（{char_count}/{PROFILE_MAX_CHARS} 字）：\n\n"
            f"{profile}"
        )
