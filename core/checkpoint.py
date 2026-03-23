# -*- coding: utf-8 -*-
"""
DocMaster Agent - 工作流 Checkpoint（断点续传）

实现类似 LangGraph 的 Checkpointing 功能：
  1. 每个工作流节点执行完后自动保存状态到 JSON 文件
  2. 如果中途崩溃，重启后自动从断点恢复
  3. 完成后自动清理 checkpoint 文件

这就是"游戏存档"——打完每一关自动存档，
死了从最近的存档点重来，不用从头开始。
"""

import os
import json
import time
from typing import Optional
from enum import Enum


class WorkflowPhase(str, Enum):
    """工作流阶段"""
    NOT_STARTED = "not_started"
    PLANNING = "planning"
    PLAN_DONE = "plan_done"
    EXECUTING = "executing"
    EXEC_DONE = "exec_done"
    REVIEWING = "reviewing"
    COMPLETED = "completed"


class WorkflowState:
    """
    工作流的完整状态快照。

    包含恢复执行所需的一切信息：
      - 当前阶段（Planning/Executing/Reviewing）
      - 文件路径
      - Planner 生成的计划
      - 已完成的步骤及结果
      - 重试计数器
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.phase = WorkflowPhase.NOT_STARTED
        self.plan: str = ""
        self.completed_steps: list[str] = []
        self.step_results: list[str] = []
        self.current_step_index: int = 0
        self.retry_counts: dict[str, int] = {}  # "step_index" -> count
        self.re_planned: bool = False
        self.report_parts: list[str] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

    def to_dict(self) -> dict:
        """序列化为字典（用于 JSON 存储）"""
        return {
            "file_path": self.file_path,
            "phase": self.phase.value,
            "plan": self.plan,
            "completed_steps": self.completed_steps,
            "step_results": self.step_results,
            "current_step_index": self.current_step_index,
            "retry_counts": self.retry_counts,
            "re_planned": self.re_planned,
            "report_parts": self.report_parts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowState":
        """从字典反序列化"""
        state = cls(data["file_path"])
        state.phase = WorkflowPhase(data["phase"])
        state.plan = data.get("plan", "")
        state.completed_steps = data.get("completed_steps", [])
        state.step_results = data.get("step_results", [])
        state.current_step_index = data.get("current_step_index", 0)
        state.retry_counts = data.get("retry_counts", {})
        state.re_planned = data.get("re_planned", False)
        state.report_parts = data.get("report_parts", [])
        state.created_at = data.get("created_at", time.time())
        state.updated_at = data.get("updated_at", time.time())
        return state


class Checkpointer:
    """
    Checkpoint 管理器 — 自动保存和恢复工作流状态。

    存储方式：JSON 文件（简单可靠，便于调试）
    存储位置：项目根目录下的 checkpoints/ 目录

    使用方式（类似游戏存档）：
      checkpointer = Checkpointer("checkpoints/")
      state = checkpointer.load("任务ID") or WorkflowState(file_path)
      # ... 执行一步 ...
      checkpointer.save("任务ID", state)  # 自动存档
      # ... 失败了 ...
      state = checkpointer.load("任务ID")  # 从存档恢复
    """

    def __init__(self, checkpoint_dir: str):
        self.checkpoint_dir = checkpoint_dir
        os.makedirs(checkpoint_dir, exist_ok=True)

    def _get_path(self, task_id: str) -> str:
        """获取 checkpoint 文件路径"""
        # 清理文件名中的非法字符
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
        return os.path.join(self.checkpoint_dir, f"ckpt_{safe_id}.json")

    def save(self, task_id: str, state: WorkflowState):
        """
        保存当前工作流状态（存档）。

        每次节点执行完都应调用此方法。
        """
        state.updated_at = time.time()
        path = self._get_path(task_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

    def load(self, task_id: str) -> Optional[WorkflowState]:
        """
        加载之前保存的工作流状态（读档）。

        Returns:
            WorkflowState 如果存在存档，否则 None
        """
        path = self._get_path(task_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return WorkflowState.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[Checkpoint] 存档损坏，忽略: {e}")
            return None

    def clear(self, task_id: str):
        """清理已完成任务的 checkpoint 文件"""
        path = self._get_path(task_id)
        if os.path.exists(path):
            os.remove(path)

    def list_checkpoints(self) -> list[dict]:
        """列出所有未完成的 checkpoint"""
        results = []
        if not os.path.isdir(self.checkpoint_dir):
            return results
        for fname in os.listdir(self.checkpoint_dir):
            if not fname.startswith("ckpt_") or not fname.endswith(".json"):
                continue
            path = os.path.join(self.checkpoint_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "task_id": fname[5:-5],  # strip "ckpt_" and ".json"
                    "file_path": data.get("file_path", ""),
                    "phase": data.get("phase", ""),
                    "steps_done": len(data.get("completed_steps", [])),
                    "updated_at": data.get("updated_at", 0),
                })
            except Exception:
                continue
        return results
