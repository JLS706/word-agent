# -*- coding: utf-8 -*-
"""
DocMaster Agent - ReAct Agent 核心
实现 ReAct (Reasoning + Acting) 循环：
  Think → Act → Observe → Think → ... → Finish
"""

import traceback

from core.llm import LLM
from core.schema import AgentState, Message, Role, ToolResult
from core.prompt import build_system_prompt
from tools.base import ToolRegistry


class Agent:
    """
    ReAct Agent —— 核心调度引擎。

    工作流程：
    1. 接收用户指令
    2. 将对话历史 + 工具列表发给 LLM
    3. 若 LLM 返回 tool_calls → 执行工具 → 结果加入对话历史 → 回到步骤 2
    4. 若 LLM 返回纯文本 → 作为最终回答返回给用户
    """

    def __init__(
        self,
        llm: LLM,
        tool_registry: ToolRegistry,
        max_steps: int = 10,
        verbose: bool = True,
        dry_run: bool = False,
        memory=None,
    ):
        self.llm = llm
        self.tools = tool_registry
        self.max_steps = max_steps
        self.verbose = verbose
        self.dry_run = dry_run
        self.memory = memory
        self.state = AgentState.IDLE
        self.history: list[Message] = []
        self._session_tools: list[str] = []   # 本轮执行过的工具名
        self._session_file: str = ""           # 本轮操作的文件路径

        # 构建系统提示词（含记忆上下文）
        tool_desc = self.tools.describe()
        memory_context = memory.get_context_summary() if memory else ""
        system_msg = Message(
            role=Role.SYSTEM,
            content=build_system_prompt(tool_desc, memory_context),
        )
        self.history.append(system_msg)

    def run(self, user_input: str) -> str:
        """
        执行一次完整的 Agent 循环。

        Args:
            user_input: 用户的自然语言指令

        Returns:
            Agent 的最终回答文本
        """
        # 添加用户消息
        self.history.append(Message(role=Role.USER, content=user_input))
        self.state = AgentState.THINKING

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"🧠 Agent 收到指令: {user_input}")
            print(f"{'='*60}")

        openai_tools = self.tools.to_openai_tools()

        for step in range(1, self.max_steps + 1):
            if self.verbose:
                print(f"\n--- 第 {step}/{self.max_steps} 步 ---")

            # 调用 LLM
            self.state = AgentState.THINKING
            try:
                response = self.llm.chat(self.history, tools=openai_tools)
            except Exception as e:
                error_msg = f"❌ LLM 调用失败: {e}"
                if self.verbose:
                    print(error_msg)
                    traceback.print_exc()
                self.state = AgentState.ERROR
                return error_msg

            # 将 LLM 回复加入历史
            self.history.append(response)

            # 情况1: LLM 返回纯文本（没有工具调用） → 任务完成
            if not response.tool_calls:
                self.state = AgentState.FINISHED
                final_answer = response.content or "(Agent 没有给出回答)"
                if self.verbose:
                    print(f"\n✅ Agent 回答:\n{final_answer}")
                # 自动保存本轮操作到记忆
                self._save_session(final_answer)
                return final_answer

            # 情况2: LLM 请求调用工具 → 逐一执行
            self.state = AgentState.ACTING
            for tc in response.tool_calls:
                result = self._execute_tool(tc.id, tc.name, tc.arguments)

                # 将工具结果加入对话历史
                tool_msg = Message(
                    role=Role.TOOL,
                    content=result.output,
                    tool_call_id=result.tool_call_id,
                    name=result.name,
                )
                self.history.append(tool_msg)

        # 超过最大步数
        self.state = AgentState.ERROR
        timeout_msg = f"⚠️ Agent 已达到最大步数 ({self.max_steps})，强制停止。"
        if self.verbose:
            print(timeout_msg)
        return timeout_msg

    def _execute_tool(self, call_id: str, name: str, arguments: dict) -> ToolResult:
        """执行单个工具调用"""
        tool = self.tools.get(name)
        if tool is None:
            error = f"未找到工具: {name}"
            if self.verbose:
                print(f"  ❌ {error}")
            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=error,
                success=False,
            )

        if self.verbose:
            args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
            print(f"  🔧 调用工具: {name}({args_str})")

        # Dry-run 模式：不实际执行
        if self.dry_run:
            output = f"[DRY-RUN] 将调用 {name}，参数: {arguments}"
            if self.verbose:
                print(f"  🏜️ {output}")
            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=output,
                success=True,
            )

        # 实际执行
        try:
            output = tool.execute(**arguments)
            if self.verbose:
                # 截断过长输出
                display = output[:300] + "..." if len(output) > 300 else output
                print(f"  ✅ 结果: {display}")

            # 记录工具执行（供记忆系统使用）
            self._session_tools.append(name)
            if "file_path" in arguments and not self._session_file:
                self._session_file = arguments["file_path"]

            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=output,
                success=True,
            )
        except Exception as e:
            error_msg = f"工具执行失败: {e}\n{traceback.format_exc()}"
            if self.verbose:
                print(f"  ❌ {error_msg[:200]}")
            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=error_msg,
                success=False,
            )

    def _save_session(self, summary: str):
        """将本轮操作记录保存到记忆"""
        if self.memory and self._session_tools:
            file_path = self._session_file or "unknown"
            self.memory.add_session(
                file_path=file_path,
                actions=self._session_tools,
                summary=summary[:200],  # 截断过长摘要
            )

    def reset(self):
        """重置 Agent 状态（保留系统提示词）"""
        system_msg = self.history[0] if self.history else None
        self.history.clear()
        if system_msg:
            self.history.append(system_msg)
        self.state = AgentState.IDLE
        self._session_tools = []
        self._session_file = ""
