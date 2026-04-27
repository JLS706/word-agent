# -*- coding: utf-8 -*-
"""
TaskRouter — 意图分类器 + Python FSM 状态机

三层路由架构的核心实现：
  第一层：约束解码 — LLM 只输出枚举值（TaskIntent），不自由发挥
  第二层：FSM 硬接管 — 每种 Intent 对应一条硬编码的 Worker 调度链
  （第三层：悲观降级 — 分类失败时默认 TASK_FULL，待实现）

设计哲学：
  大模型只是一个聪明的「道岔开关」，负责选轨道；
  而轨道上有几个检查站，全是 Python 代码焊死的。
"""

import json
from enum import Enum
from typing import Optional

from core.logger import logger
from core.schema import Message, Role


# ═════════════════════════════════════════════
# 第一层：任务意图枚举（约束解码的目标空间）
# ═════════════════════════════════════════════

class TaskIntent(Enum):
    """Coordinator 意图分类的输出空间（单选题）。"""
    TASK_FULL         = "full"           # 全量：Plan → Execute → Review
    TASK_REVIEW_ONLY  = "review_only"    # 仅审查
    TASK_FORMAT_ONLY  = "format_only"    # 仅排版（Execute → Review）
    TASK_EXECUTE_ONLY = "execute_only"   # 仅执行（无 Plan / Review）
    TASK_SIMPLE       = "simple"         # 简单任务：Coordinator 直接调工具，不 Fork


# ═════════════════════════════════════════════
# 第二层：FSM 状态机 — 每种 Intent 对应的硬编码调度链
# ═════════════════════════════════════════════

# (role, objective_template) — objective_template 中 {user_input} 和 {target_file} 会被替换
_INTENT_PIPELINES: dict[TaskIntent, list[tuple[str, str]]] = {
    TaskIntent.TASK_FULL: [
        ("Planner",  "分析文档现状并制定执行计划，文件: {target_file}"),
        ("Executor", "按计划执行排版操作，文件: {target_file}。用户需求: {user_input}"),
        ("Reviewer", "审查执行结果，L1 铁律一票否决，文件: {target_file}"),
    ],
    TaskIntent.TASK_REVIEW_ONLY: [
        ("Reviewer", "全面审查文档格式与内容，文件: {target_file}。用户需求: {user_input}"),
    ],
    TaskIntent.TASK_FORMAT_ONLY: [
        ("Executor", "执行排版操作，文件: {target_file}。用户需求: {user_input}"),
        ("Reviewer", "审查排版结果，文件: {target_file}"),
    ],
    TaskIntent.TASK_EXECUTE_ONLY: [
        ("Executor", "执行指定操作，文件: {target_file}。用户需求: {user_input}"),
    ],
    # TASK_SIMPLE 不走 pipeline，由 Coordinator 直接 ReAct
}


# ═════════════════════════════════════════════
# 意图分类器（Function Calling 约束解码）
# ═════════════════════════════════════════════

# 用于约束解码的 classify_intent "伪工具"定义
_CLASSIFY_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "description": (
            "根据用户输入，将任务分类为以下类型之一。"
            "你只需要做分类，不需要执行任何操作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [e.value for e in TaskIntent],
                    "description": (
                        "任务意图类型：\n"
                        "- full: 需要完整的 Plan→Execute→Review 全流程（如「帮我全面排版这篇论文」）\n"
                        "- review_only: 仅需审查/检查文档（如「帮我检查下格式对不对」「审查这篇论文」）\n"
                        "- format_only: 仅需排版操作（如「格式化参考文献」「处理图注」）\n"
                        "- execute_only: 执行单个指定操作、不需要审查（如「把LaTeX公式转MathType」）\n"
                        "- simple: 简单对话/查询、不涉及文档处理流水线（如「关闭Word」「你好」「查看规则」）"
                    ),
                },
                "target_file": {
                    "type": "string",
                    "description": "用户提到的目标文件路径。如果用户没有指定文件路径，返回空字符串。",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话解释分类理由（调试用，不超过 30 字）。",
                },
            },
            "required": ["intent", "target_file", "reason"],
        },
    },
}


def classify_intent(
    llm,
    user_input: str,
    history_context: str = "",
) -> tuple[TaskIntent, str, str]:
    """
    调用 LLM 进行意图分类（约束解码，只输出枚举值）。

    Args:
        llm: LLM 实例
        user_input: 用户原始输入
        history_context: 最近的对话摘要（帮助理解上下文，如已知文件路径）

    Returns:
        (intent, target_file, reason)
    """
    system_msg = Message(
        role=Role.SYSTEM,
        content=(
            "你是一个任务意图分类器。根据用户输入，调用 classify_intent 工具进行分类。\n"
            "你只需要分类，不需要回答用户的问题或执行任何操作。\n"
            "注意：\n"
            "- 涉及「全面处理」「完整排版」等需要多步骤的 → full\n"
            "- 涉及「检查」「审查」「验证」「看看格式对不对」 → review_only\n"
            "- 涉及单个具体排版操作（参考文献、图注、交叉引用）→ format_only\n"
            "- 涉及单个非排版操作（LaTeX转换、缩写检测）→ execute_only\n"
            "- 简单对话/查询/不涉及文档流水线 → simple"
        ),
    )

    user_content = user_input
    if history_context:
        user_content = f"[上下文] {history_context}\n\n[用户输入] {user_input}"

    user_msg = Message(role=Role.USER, content=user_content)

    try:
        response = llm.chat(
            [system_msg, user_msg],
            tools=[_CLASSIFY_TOOL],
        )

        # 解析 tool_calls
        if response.tool_calls:
            tc = response.tool_calls[0]
            if tc.name == "classify_intent":
                args = tc.arguments
                intent_str = args.get("intent", "full")
                target_file = args.get("target_file", "")
                reason = args.get("reason", "")

                try:
                    intent = TaskIntent(intent_str)
                except ValueError:
                    logger.warning(
                        "[Router] LLM 返回非法 intent: %s，降级为 TASK_FULL",
                        intent_str,
                    )
                    intent = TaskIntent.TASK_FULL

                logger.info(
                    "[Router] intent=%s | file=%s | reason=%s",
                    intent.value, target_file or "(none)", reason,
                )
                return intent, target_file, reason

        # LLM 没有调用 classify_intent → 降级
        logger.warning("[Router] LLM 未调用 classify_intent，降级为 TASK_SIMPLE")
        return TaskIntent.TASK_SIMPLE, "", "LLM 未返回分类结果"

    except Exception as e:
        logger.error("[Router] 意图分类失败: %s，降级为 TASK_FULL", e)
        return TaskIntent.TASK_FULL, "", f"分类异常: {e}"


# ═════════════════════════════════════════════
# FSM 调度器
# ═════════════════════════════════════════════

class TaskFSM:
    """
    任务状态机：根据 Intent 驱动 Worker 调度链。

    用法：
        fsm = TaskFSM(intent, user_input, target_file)
        for step in fsm:
            role, objective = step
            report = delegate_task(role=role, objective=objective, target_file=...)
            fsm.feed_report(report)
    """

    def __init__(
        self,
        intent: TaskIntent,
        user_input: str,
        target_file: str,
    ):
        self.intent = intent
        self.user_input = user_input
        self.target_file = target_file
        self._pipeline = list(_INTENT_PIPELINES.get(intent, []))
        self._current_step = 0
        self._reports: list[dict] = []

        logger.info(
            "[FSM] init: intent=%s, steps=%d, file=%s",
            intent.value, len(self._pipeline), target_file,
        )

    @property
    def is_pipeline_intent(self) -> bool:
        """是否需要走 pipeline（非 SIMPLE 都需要）。"""
        return self.intent != TaskIntent.TASK_SIMPLE

    @property
    def total_steps(self) -> int:
        return len(self._pipeline)

    @property
    def current_step(self) -> int:
        return self._current_step

    @property
    def is_done(self) -> bool:
        return self._current_step >= len(self._pipeline)

    @property
    def reports(self) -> list[dict]:
        return list(self._reports)

    def __iter__(self):
        return self

    def __next__(self) -> tuple[str, str]:
        """
        返回下一步的 (role, objective)。

        Raises:
            StopIteration: 所有步骤已完成
        """
        if self.is_done:
            raise StopIteration

        role, obj_template = self._pipeline[self._current_step]
        objective = obj_template.format(
            user_input=self.user_input,
            target_file=self.target_file,
        )

        logger.info(
            "[FSM] >> Step %d/%d: role=%s",
            self._current_step + 1, self.total_steps, role,
        )
        return role, objective

    def feed_report(self, report: dict):
        """
        接收 Worker 报告并推进状态。

        Args:
            report: Worker 返回的 JSON 报告 dict
        """
        self._reports.append(report)
        status = report.get("status", "UNKNOWN")

        logger.info(
            "[FSM] << Step %d/%d done: status=%s",
            self._current_step + 1, self.total_steps, status,
        )

        # Planner FAIL → 仍然继续（降级为无计划执行）
        # Executor FAIL → 仍然继续到 Reviewer（让 Reviewer 记录失败）
        # 状态机不因某一步失败而中断整条链路
        self._current_step += 1

    def build_summary(self) -> str:
        """
        汇总所有 Worker 报告，生成给 Coordinator 的结构化摘要。
        """
        if not self._reports:
            return "（无 Worker 报告）"

        lines = [f"[FSM] 执行摘要（{self.intent.value}，共 {len(self._reports)} 步）：\n"]
        for i, report in enumerate(self._reports):
            role = self._pipeline[i][0] if i < len(self._pipeline) else "?"
            status = report.get("status", "UNKNOWN")
            summary = report.get("summary", "无摘要")
            emoji = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
            lines.append(f"  {emoji} Step {i+1} [{role}]: {summary}")

            issues = report.get("issues_found", [])
            if issues:
                for issue in issues[:3]:  # 最多展示 3 条
                    lines.append(f"      └─ {issue}")

        return "\n".join(lines)
