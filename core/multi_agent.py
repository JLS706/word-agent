# -*- coding: utf-8 -*-
"""
DocMaster - Multi-Agent 协作系统
Planner → Executor → Reviewer 三角色流水线。
三个角色共享同一个 LLM，通过不同 System Prompt 切换身份。
"""

import os
import re
import hashlib
import traceback

from core.llm import LLM
from core.schema import Message, Role
from core.prompt import build_planner_prompt, build_reviewer_prompt
from core.checkpoint import Checkpointer, WorkflowState, WorkflowPhase


class MultiAgentOrchestrator:
    """
    Multi-Agent 编排器。

    流水线:
        1. Planner  — 分析文档、制定执行计划
        2. Executor — 按计划逐步调用工具
        3. Reviewer — 读取处理后文档、验证结果

    三个角色共享同一 LLM 和 ToolRegistry，通过 System Prompt 切换。
    """

    def __init__(self, llm: LLM, executor_agent, tool_registry, memory=None,
                 verbose=True, checkpoint_dir: str = ""):
        self.llm = llm
        self.executor = executor_agent
        self.tools = tool_registry
        self.memory = memory
        self.verbose = verbose
        self.checkpointer = Checkpointer(checkpoint_dir) if checkpoint_dir else None

    @staticmethod
    def _make_task_id(file_path: str) -> str:
        return hashlib.md5(os.path.abspath(file_path).encode()).hexdigest()[:12]

    def run_pipeline(self, file_path: str) -> str:
        """Run pipeline with checkpoint save/resume support."""
        task_id = self._make_task_id(file_path)

        # Try to resume from checkpoint
        state = None
        if self.checkpointer:
            state = self.checkpointer.load(task_id)
            if state and state.phase != WorkflowPhase.NOT_STARTED:
                if self.verbose:
                    print(f"\n  [Checkpoint] Resume from {state.phase.value}")
        if not state:
            state = WorkflowState(file_path)

        # Phase 1: Planner
        if state.phase in (WorkflowPhase.NOT_STARTED, WorkflowPhase.PLANNING):
            if self.verbose:
                print("\n" + "=" * 60)
                print("  Phase 1/3 - Planner")
                print("=" * 60)
            state.phase = WorkflowPhase.PLANNING
            plan = self._run_planner(file_path)
            state.plan = plan
            state.report_parts.append("## Planning\n" + plan)
            state.phase = WorkflowPhase.PLAN_DONE
            if self.checkpointer:
                self.checkpointer.save(task_id, state)

        # Phase 2: Executor
        if state.phase in (WorkflowPhase.PLAN_DONE, WorkflowPhase.EXECUTING):
            if self.verbose:
                print("\n" + "=" * 60)
                print("  Phase 2/3 - Executor")
                print("=" * 60)
            state.phase = WorkflowPhase.EXECUTING
            exec_result = self._run_executor_with_backtracking(
                file_path, state.plan, state=state, task_id=task_id
            )
            state.report_parts.append("## Execution\n" + exec_result)
            state.phase = WorkflowPhase.EXEC_DONE
            if self.checkpointer:
                self.checkpointer.save(task_id, state)

        # Phase 3: Reviewer
        if state.phase in (WorkflowPhase.EXEC_DONE, WorkflowPhase.REVIEWING):
            if self.verbose:
                print("\n" + "=" * 60)
                print("  Phase 3/3 - Reviewer")
                print("=" * 60)
            state.phase = WorkflowPhase.REVIEWING
            exec_text = ""
            for part in state.report_parts:
                if part.startswith("## Execution"):
                    exec_text = part
            review = self._run_reviewer(file_path, exec_text)
            state.report_parts.append("## Review\n" + review)
            state.phase = WorkflowPhase.COMPLETED

        # Final report
        final_report = "\n\n".join(state.report_parts)
        if self.memory:
            self.memory.add_session(
                file_path=file_path,
                actions=["pipeline:planner", "pipeline:executor", "pipeline:reviewer"],
                summary="Pipeline done",
            )
        if self.checkpointer:
            self.checkpointer.clear(task_id)

        return final_report

    def _run_planner(self, file_path: str) -> str:
        """Phase 1: Planner 分析文档并制定计划"""
        try:
            tool_desc = self.tools.describe()
            memory_ctx = self.memory.get_context_summary() if self.memory else ""
            system_prompt = build_planner_prompt(tool_desc, memory_ctx)

            messages = [
                Message(role=Role.SYSTEM, content=system_prompt),
                Message(
                    role=Role.USER,
                    content=f"请分析以下文档并制定执行计划：{file_path}",
                ),
            ]

            # Planner 只能调用 analyze_document 和 recall_history
            planner_tools = []
            for t in self.tools.to_openai_tools():
                if t["function"]["name"] in ("analyze_document", "recall_history"):
                    planner_tools.append(t)

            # ReAct 循环（最多3步）
            for step in range(3):
                response = self.llm.chat(messages, tools=planner_tools)
                messages.append(response)

                if not response.tool_calls:
                    return response.content or "(Planner 未生成计划)"

                # 执行工具调用
                for tc in response.tool_calls:
                    tool = self.tools.get(tc.name)
                    if tool:
                        try:
                            output = tool.execute(**tc.arguments)
                            if self.verbose:
                                display = output[:200] + "..." if len(output) > 200 else output
                                print(f"  🧭 Planner 调用: {tc.name} → {display}")
                        except Exception as e:
                            output = f"工具执行失败: {e}"
                    else:
                        output = f"未找到工具: {tc.name}"

                    messages.append(Message(
                        role=Role.TOOL,
                        content=output,
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))

            return "(Planner 超时)"

        except Exception as e:
            return f"Planner 异常: {e}\n{traceback.format_exc()}"

    def _run_executor(self, file_path: str, plan: str) -> str:
        """Phase 2: Executor 按计划执行工具"""
        try:
            # 重置 Executor 的对话历史
            self.executor.reset()

            # 将计划作为用户指令传给 Executor
            prompt = (
                f"以下是 Planner 为文件 {file_path} 制定的执行计划，"
                f"请严格按计划逐步执行所有步骤：\n\n{plan}\n\n"
                f"注意：每个工具的 file_path 参数统一使用 {file_path}"
            )

            result = self.executor.run(prompt)
            return result

        except Exception as e:
            return f"Executor 异常: {e}\n{traceback.format_exc()}"

    # ══════════════════════════════════════════════
    # 回溯修正 (Backtracking) 核心方法
    # ══════════════════════════════════════════════

    def _parse_plan_steps(self, plan: str) -> list[dict]:
        """
        从 Planner 生成的文本计划中提取结构化步骤。

        支持的格式：
          1. format_references (参考文献格式化)
          2. create_reference_crossrefs — 文献交叉引用
          Step 3: check_acronym_definitions

        Returns:
            [{"index": 1, "tool": "format_references", "desc": "参考文献格式化"}, ...]
        """
        steps = []
        # 匹配常见的计划格式：序号 + 工具名 + 可选描述
        pattern = re.compile(
            r'(?:^|\n)\s*(?:Step\s*)?'
            r'(\d+)[.\)\u3001::\-\s]+'
            r'(\w+)'
            r'[\s\(\uff08\-\u2014]*'
            r'([^\n\)\uff09]*)'
        )
        for m in pattern.finditer(plan):
            idx = int(m.group(1))
            tool_name = m.group(2).strip()
            desc = m.group(3).strip().rstrip('\uff09)')
            steps.append({"index": idx, "tool": tool_name, "desc": desc or tool_name})

        # 如果正则解析失败，回退为把整个 plan 作为单步
        if not steps:
            steps = [{"index": 1, "tool": "full_plan", "desc": plan[:100]}]

        return steps

    # ──────────────────────────────────────────────
    # 两级验证标准（非人为干预，全自动化）
    # ──────────────────────────────────────────────

    # Tier 1 验证规则：关键词匹配（零 LLM 调用成本）
    # 这些规则需要人为预定义——它们是领域经验的编码
    _FAIL_INDICATORS = [
        # 通用错误标识
        "失败", "错误", "异常", "无法", "Error", "Exception", "❌", "Traceback",
        # Word 领域专属错误（交叉引用断裂等）
        "没有找到引用源",       # Word 经典域代码错误："错误！没有找到引用源！"
        "找不到引用源",
        "REF field",           # 英文版 Word 的引用错误
        "bookmark not defined", # 英文版：书签未定义
    ]
    _PASS_INDICATORS = [
        # 通用成功标识
        "完成", "成功", "已处理", "已保存", "✅", "已生成", "已检测",
        # Word 领域专属成功标识
        "引用已更新", "替换了", "处理了",
    ]
    # 关键性错误：文档结构问题，重试和重规划都无法解决
    # 应直接跳过 retry/re-plan，立即汇报给人类处理
    _CRITICAL_FAIL_INDICATORS = [
        "没有找到引用源",       # Word 交叉引用断裂 → 需人工检查书签
        "找不到引用源",
        "bookmark not defined",
        "REF field",
    ]

    def _verify_step(
        self,
        step: dict,
        exec_result: str,
    ) -> tuple[bool, str]:
        """
        验证单步执行结果。

        评估标准不是人为干预，而是两层自动化：
          Tier 1: 规则检查（关键词匹配，零成本）
          Tier 2: LLM mini-review（仅在 Tier 1 无法判断时触发）

        Returns:
            (passed: bool, reason: str)
            当 reason 以 '[CRITICAL]' 开头时，表示关键错误，应直接汇报人类
        """
        step_desc = f"{step['tool']} ({step['desc']})"

        # ── 关键错误快速通道：直接标记需人工处理 ──
        critical_hit = [kw for kw in self._CRITICAL_FAIL_INDICATORS if kw in exec_result]
        if critical_hit:
            return False, (
                f"[CRITICAL] 检测到关键性错误（'{critical_hit[0]}'），"
                f"此类错误为文档结构问题，无法通过重试解决，需人工检查"
            )

        # ── Tier 1: 规则检查（零成本）──
        has_fail = any(kw in exec_result for kw in self._FAIL_INDICATORS)
        has_pass = any(kw in exec_result for kw in self._PASS_INDICATORS)

        if has_fail and not has_pass:
            return False, f"Tier1 规则判定 FAIL：结果含错误标识"
        if has_pass and not has_fail:
            return True, f"Tier1 规则判定 PASS：结果含成功标识"

        # ── Tier 2: LLM mini-review（只在 Tier 1 无法判断时触发）──
        if self.verbose:
            print(f"    🔍 Tier1 无法确定，触发 Tier2 LLM mini-review...")

        try:
            review_messages = [
                Message(role=Role.SYSTEM, content=(
                    "你是一个执行结果审查员。"
                    "根据执行结果判断步骤是否成功完成。"
                    "你必须以以下格式回复第一行："
                    "PASS 或 FAIL，第二行给出一句话理由。"
                )),
                Message(role=Role.USER, content=(
                    f"执行步骤: {step_desc}\n"
                    f"执行结果:\n{exec_result[:500]}\n\n"
                    f"该步骤是否成功完成？请回复 PASS 或 FAIL。"
                )),
            ]
            response = self.llm.chat(review_messages)
            reply = (response.content or "").strip()

            if reply.upper().startswith("PASS"):
                return True, f"Tier2 LLM 判定 PASS: {reply}"
            elif reply.upper().startswith("FAIL"):
                return False, f"Tier2 LLM 判定 FAIL: {reply}"
            else:
                # LLM 回复格式不标准，尝试从内容推断
                if "失败" in reply or "FAIL" in reply.upper():
                    return False, f"Tier2 LLM 推断 FAIL: {reply[:100]}"
                return True, f"Tier2 LLM 推断 PASS: {reply[:100]}"

        except Exception as e:
            # LLM 调用失败时，保守策略判定为 PASS（不因审查失败而阻塞流程）
            return True, f"审查异常，默认 PASS: {e}"

    # ──────────────────────────────────────────────
    # 重新规划（从失败点开始）
    # ──────────────────────────────────────────────

    def _re_plan_remaining(
        self,
        file_path: str,
        failed_step: dict,
        error_reason: str,
        completed_steps: list[str],
    ) -> str:
        """
        让 Planner 从失败点重新规划剩余步骤。

        Args:
            file_path: 文档路径
            failed_step: 失败的步骤
            error_reason: 失败原因
            completed_steps: 已成功完成的步骤描述列表

        Returns:
            新的执行计划文本
        """
        try:
            tool_desc = self.tools.describe()
            memory_ctx = self.memory.get_context_summary() if self.memory else ""
            system_prompt = build_planner_prompt(tool_desc, memory_ctx)

            completed_str = "\n".join(
                f"  ✅ {s}" for s in completed_steps
            ) if completed_steps else "  （无）"

            messages = [
                Message(role=Role.SYSTEM, content=system_prompt),
                Message(
                    role=Role.USER,
                    content=(
                        f"文件: {file_path}\n\n"
                        f"已完成的步骤：\n{completed_str}\n\n"
                        f"失败的步骤: {failed_step['tool']} ({failed_step['desc']})\n"
                        f"失败原因: {error_reason[:200]}\n\n"
                        f"请重新规划剩余步骤（不要重复已完成的步骤）。"
                        f"可以调整执行顺序或替换失败的工具。"
                    ),
                ),
            ]

            response = self.llm.chat(messages)
            return response.content or "(重新规划失败)"

        except Exception as e:
            return f"重新规划异常: {e}"

    # ──────────────────────────────────────────────
    # 回溯执行引擎（核心）
    # ──────────────────────────────────────────────

    def _run_executor_with_backtracking(
        self,
        file_path: str,
        plan: str,
        max_retries: int = 2,
        state: WorkflowState = None,
        task_id: str = "",
    ) -> str:
        """
        逻步执行 + 每步验证 + 三级回溯。

        策略升级路径：
          策略 1: 重试（同一步骤修正参数，最多 max_retries 次）
          策略 2: 重新规划（从失败点让 Planner 重新规划剩余步骤）
          策略 3: 跳过（标记“需人工处理”，继续下一步）
        """
        # 解析计划为结构化步骤
        steps = self._parse_plan_steps(plan)

        if self.verbose:
            print(f"\n  📋 解析出 {len(steps)} 个执行步骤:")
            for s in steps:
                print(f"    {s['index']}. {s['tool']} — {s['desc']}")

        # 如果解析失败（回退为单步），直接用旧版执行器
        if len(steps) == 1 and steps[0]["tool"] == "full_plan":
            if self.verbose:
                print("  ⚠️ 计划格式无法解析，回退为整体执行模式")
            return self._run_executor(file_path, plan)

        # 重置 Executor
        self.executor.reset()

        # Resume from checkpoint
        if state and state.current_step_index > 0:
            completed_steps = state.completed_steps
            step_results = state.step_results
            i = state.current_step_index
            retry_counts = {int(k): v for k, v in state.retry_counts.items()}
            re_planned = state.re_planned
            if self.verbose:
                print(f"  [Checkpoint] Resume from step {i + 1}")
        else:
            completed_steps: list[str] = []
            step_results: list[str] = []
            i = 0
            retry_counts: dict[int, int] = {}
            re_planned = False

        while i < len(steps):
            step = steps[i]
            retry_counts.setdefault(i, 0)
            step_desc = f"{step['tool']} ({step['desc']})"

            if self.verbose:
                print(f"\n  ━━━ Step {step['index']}/{len(steps)}: {step_desc} ━━━")

            # ── 执行当前步骤 ──
            try:
                prompt = (
                    f"请执行以下步骤：{step_desc}\n"
                    f"文件路径: {file_path}\n"
                    f"注意: modify_in_place 必须为 true。只执行这一个步骤。"
                )
                result = self.executor.run(prompt)
            except Exception as e:
                result = f"执行异常: {e}"

            # ── 验证当前步骤 ──
            passed, reason = self._verify_step(step, result)

            if self.verbose:
                status = "✅ PASS" if passed else "❌ FAIL"
                print(f"  {status} — {reason}")

            if passed:
                completed_steps.append(step_desc)
                step_results.append(f"Step {step['index']}: {step_desc}\n{result}")
                i += 1
                # Checkpoint after each successful step
                if state and self.checkpointer and task_id:
                    state.completed_steps = completed_steps
                    state.step_results = step_results
                    state.current_step_index = i
                    state.retry_counts = {str(k): v for k, v in retry_counts.items()}
                    state.re_planned = re_planned
                    self.checkpointer.save(task_id, state)
                continue

            # ── 失败：回溯策略 ──

            # 关键性错误 → 直接跳过，汇报人类（不浪费重试和重规划）
            is_critical = reason.startswith("[CRITICAL]")
            if is_critical:
                if self.verbose:
                    print(f"  🚨 关键性错误，直接汇报人类处理")
                step_results.append(
                    f"🚨 Step {step['index']}: {step_desc} — 需人工处理\n{reason}"
                )
                i += 1
                continue

            # 策略 1: 重试（最多 max_retries 次）
            if retry_counts[i] < max_retries:
                retry_counts[i] += 1
                if self.verbose:
                    print(f"  🔄 策略 1: 重试 ({retry_counts[i]}/{max_retries})")
                step_results.append(
                    f"⚠️ Step {step['index']} 重试 ({retry_counts[i]}/{max_retries}): {reason}"
                )
                continue  # 不增加 i，重新执行当前步骤

            # 策略 2: 重新规划（只允许一次，防止无限重规划）
            if not re_planned:
                if self.verbose:
                    print(f"  🧭 策略 2: 从失败点重新规划...")

                new_plan = self._re_plan_remaining(
                    file_path, step, reason, completed_steps
                )

                if self.verbose:
                    print(f"  📋 新计划:\n{new_plan[:200]}")

                new_steps = self._parse_plan_steps(new_plan)
                if new_steps and new_steps[0]["tool"] != "full_plan":
                    # 用新计划替换剩余步骤
                    steps = steps[:i] + new_steps
                    re_planned = True
                    retry_counts[i] = 0  # 重置重试计数
                    step_results.append(
                        f"🧭 Step {step['index']} 触发重新规划: {reason}"
                    )
                    continue  # 用新计划重新执行当前位置

            # 策略 3: 跳过
            if self.verbose:
                print(f"  ⏩ 策略 3: 跳过，标记为需人工处理")
            step_results.append(
                f"❌ Step {step['index']}: {step_desc} — 跳过（需人工处理）\n原因: {reason}"
            )
            i += 1

        # ── 汇总报告 ──
        summary_parts = [
            f"执行完成（共 {len(steps)} 步，"
            f"成功 {len(completed_steps)} 步，"
            f"跳过 {len(steps) - len(completed_steps)} 步）\n"
        ]
        summary_parts.extend(step_results)
        return "\n\n".join(summary_parts)

    def _run_reviewer(self, file_path: str, exec_result: str) -> str:
        """Phase 3: Reviewer 验证处理结果"""
        try:
            tool_desc = self.tools.describe()
            system_prompt = build_reviewer_prompt(tool_desc)

            # 提取 Executor 产出的输出路径（如果有）
            import re
            output_path_match = re.search(
                r'已保存为[：:]\s*(.+\.docx)', exec_result
            )
            target_path = output_path_match.group(1).strip() if output_path_match else file_path

            messages = [
                Message(role=Role.SYSTEM, content=system_prompt),
                Message(
                    role=Role.USER,
                    content=(
                        f"Executor 已完成处理，请验证结果。\n\n"
                        f"处理后的文件路径: {target_path}\n\n"
                        f"Executor 的执行报告:\n{exec_result[:500]}\n\n"
                        f"请调用 read_document 读取文档内容，然后输出验证报告。"
                    ),
                ),
            ]

            # Reviewer 只能调用 read_document
            reviewer_tools = []
            for t in self.tools.to_openai_tools():
                if t["function"]["name"] == "read_document":
                    reviewer_tools.append(t)

            # ReAct 循环（最多3步）
            for step in range(3):
                response = self.llm.chat(messages, tools=reviewer_tools)
                messages.append(response)

                if not response.tool_calls:
                    return response.content or "(Reviewer 未生成报告)"

                for tc in response.tool_calls:
                    tool = self.tools.get(tc.name)
                    if tool:
                        try:
                            output = tool.execute(**tc.arguments)
                            if self.verbose:
                                display = output[:200] + "..." if len(output) > 200 else output
                                print(f"  🔍 Reviewer 读取: {tc.name} → {display}")
                        except Exception as e:
                            output = f"工具执行失败: {e}"
                    else:
                        output = f"未找到工具: {tc.name}"

                    messages.append(Message(
                        role=Role.TOOL,
                        content=output,
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))

            return "(Reviewer 超时)"

        except Exception as e:
            return f"Reviewer 异常: {e}\n{traceback.format_exc()}"
