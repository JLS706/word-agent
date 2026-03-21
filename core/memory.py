# -*- coding: utf-8 -*-
"""
DocMaster Agent - 本地记忆系统
将用户的操作历史和偏好保存到本地 JSON 文件中，实现跨会话记忆。
"""

import json
import os
from datetime import datetime
from typing import Optional


class Memory:
    """本地记忆管理器"""

    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir = os.path.abspath(memory_dir)
        self.history_file = os.path.join(self.memory_dir, "history.json")
        self._data = self._load()

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

    def get_context_summary(self) -> str:
        """生成记忆摘要，注入到 System Prompt 中"""
        recent = self.get_recent_sessions(3)
        if not recent:
            return ""

        lines = []
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

        result = "最近操作记录:\n" + "\n".join(lines)
        if pref_lines:
            result += "\n\n用户偏好:\n" + "\n".join(pref_lines)
        return result
