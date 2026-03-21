# -*- coding: utf-8 -*-
"""
DocMaster - Multi-Agent 协作系统
Planner → Executor → Reviewer 三角色流水线。
三个角色共享同一个 LLM，通过不同 System Prompt 切换身份。
"""

import traceback

from core.llm import LLM
from core.schema import Message, Role
from core.prompt import build_planner_prompt, build_reviewer_prompt


class MultiAgentOrchestrator:
    """
    Multi-Agent 编排器。

    流水线:
        1. Planner  — 分析文档、制定执行计划
        2. Executor — 按计划逐步调用工具
        3. Reviewer — 读取处理后文档、验证结果

    三个角色共享同一 LLM 和 ToolRegistry，通过 System Prompt 切换。
    """

    def __init__(self, llm: LLM, executor_agent, tool_registry, memory=None, verbose=True):
        """
        Args:
            llm: 共享的 LLM 实例
            executor_agent: 已创建的 Executor Agent 实例
            tool_registry: 工具注册表
            memory: 记忆系统
            verbose: 是否打印详细日志
        """
        self.llm = llm
        self.executor = executor_agent
        self.tools = tool_registry
        self.memory = memory
        self.verbose = verbose

    def run_pipeline(self, file_path: str) -> str:
        """
        运行完整的 Multi-Agent 流水线。

        Args:
            file_path: 要处理的 Word 文档路径

        Returns:
            最终的综合报告
        """
        report_parts = []

        # ═══════════ Phase 1: Planner ═══════════
        if self.verbose:
            print("\n" + "═" * 60)
            print("  🧭 Phase 1/3 — Planner（规划者）正在分析文档...")
            print("═" * 60)

        plan = self._run_planner(file_path)
        report_parts.append("## 📋 规划阶段\n" + plan)

        if self.verbose:
            print(f"\n📋 执行计划:\n{plan}")

        # ═══════════ Phase 2: Executor ═══════════
        if self.verbose:
            print("\n" + "═" * 60)
            print("  ⚡ Phase 2/3 — Executor（执行者）正在执行计划...")
            print("═" * 60)

        exec_result = self._run_executor(file_path, plan)
        report_parts.append("## 🔧 执行阶段\n" + exec_result)

        # ═══════════ Phase 3: Reviewer ═══════════
        if self.verbose:
            print("\n" + "═" * 60)
            print("  🔍 Phase 3/3 — Reviewer（审查者）正在验证结果...")
            print("═" * 60)

        review = self._run_reviewer(file_path, exec_result)
        report_parts.append("## ✅ 验证阶段\n" + review)

        # ═══════════ 综合报告 ═══════════
        final_report = "\n\n".join(report_parts)

        if self.verbose:
            print("\n" + "═" * 60)
            print("  📊 Multi-Agent 流水线完成！")
            print("═" * 60)
            print(final_report)

        # 保存到记忆
        if self.memory:
            self.memory.add_session(
                file_path=file_path,
                actions=["pipeline:planner", "pipeline:executor", "pipeline:reviewer"],
                summary=f"Multi-Agent流水线处理完成",
            )

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
