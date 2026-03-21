# -*- coding: utf-8 -*-
"""
Tool: 记忆查询与偏好保存
让 Agent 能查询历史操作记录和保存用户偏好。
"""

import os

from tools.base import Tool


class RecallHistoryTool(Tool):
    name = "recall_history"
    description = (
        "查询历史操作记录。可以查看最近处理过的文件和执行的操作。"
        "当用户没有提供文件路径时，可以用此工具查找上次处理的文件。"
        "当用户问'上次做了什么'时也应调用此工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "要查询的最近记录条数，默认5条",
            },
        },
        "required": [],
    }

    def __init__(self, memory=None):
        self._memory = memory

    def execute(self, count: int = 5) -> str:
        if self._memory is None:
            return "记忆系统未初始化。"

        recent = self._memory.get_recent_sessions(count)
        if not recent:
            return "暂无历史操作记录。这是第一次使用。"

        lines = ["=== 历史操作记录 ==="]
        for i, s in enumerate(reversed(recent), 1):
            actions_str = ", ".join(s["actions"]) if s["actions"] else "无"
            lines.append(
                f"{i}. [{s['time']}] {os.path.basename(s['file'])}\n"
                f"   操作: {actions_str}\n"
                f"   结果: {s['summary']}"
            )

        last_file = self._memory.get_last_file()
        if last_file:
            lines.append(f"\n上次处理的完整路径: {last_file}")

        return "\n".join(lines)


class SavePreferenceTool(Tool):
    name = "save_preference"
    description = (
        "保存用户偏好设置到本地文件。例如用户说'以后默认覆盖原文件'，"
        "就保存 modify_in_place=true。下次启动时 Agent 会记住这些偏好。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "偏好名称，如 'modify_in_place', 'default_file'",
            },
            "value": {
                "type": "string",
                "description": "偏好值",
            },
        },
        "required": ["key", "value"],
    }

    def __init__(self, memory=None):
        self._memory = memory

    def execute(self, key: str, value: str) -> str:
        if self._memory is None:
            return "记忆系统未初始化。"

        self._memory.set_preference(key, value)
        return f"已保存偏好: {key} = {value}"
