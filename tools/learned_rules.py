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

    lines = ["## 📝 我学到的经验（自学习规则）\n"]
    lines.append("以下是你在之前的对话中自己总结并保存的经验规则，请在工作中参考：\n")
    for i, rule in enumerate(rules, 1):
        lines.append(f"{i}. {rule['rule']}")
        if rule.get("context"):
            lines.append(f"   （背景：{rule['context']}）")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Tool 1: 保存学习到的规则
# ─────────────────────────────────────────────

class SaveLearnedRuleTool(Tool):
    name = "save_learned_rule"
    description = (
        "保存你在工作中领悟到的经验规则。这些规则会在下次启动时自动加载到你的记忆中，"
        "让你变得越来越聪明。\n"
        "使用场景：\n"
        "- 你发现用户总是有某种偏好（如：总是要求检查页眉页脚）\n"
        "- 你总结出某个反复出现的模式（如：该用户的论文引用格式是GB/T 7714）\n"
        "- 你发现了一个更好的操作流程\n"
        "- 用户纠正了你的错误，你想记住正确做法\n"
        "注意：只保存真正有价值的、可复用的经验，不要保存一次性信息。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "rule": {
                "type": "string",
                "description": "你学到的规则或经验。用简洁的一句话描述，例如：'用户偏好在交叉引用后保留原有的字体格式'",
            },
            "context": {
                "type": "string",
                "description": "可选。这条规则是在什么情况下学到的，方便将来回忆背景。",
            },
        },
        "required": ["rule"],
    }

    def execute(self, rule: str, context: str = "") -> str:
        if not rule or not rule.strip():
            return "❌ 规则内容不能为空。"

        rules = _load_rules()

        # 检查是否已有类似规则（简单去重）
        for existing in rules:
            if existing["rule"].strip() == rule.strip():
                return f"⚠️ 这条规则已经存在了：'{rule}'"

        # 检查数量限制
        if len(rules) >= MAX_RULES:
            return (
                f"⚠️ 已达到规则上限（{MAX_RULES}条）。请先用 forget_learned_rule "
                f"删除不再需要的规则，再保存新规则。"
            )

        # 保存新规则
        new_rule = {
            "rule": rule.strip(),
            "context": context.strip() if context else "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        rules.append(new_rule)
        _save_rules(rules)

        return f"✅ 已记住新规则（第 {len(rules)} 条）：'{rule}'"


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
