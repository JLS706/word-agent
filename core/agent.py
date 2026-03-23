# -*- coding: utf-8 -*-
"""
DocMaster Agent - ReAct Agent 核心
实现 ReAct (Reasoning + Acting) 循环：
  Think → Act → Observe → Think → ... → Finish
"""

import re
import time
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
        skill_manager=None,
    ):
        self.llm = llm
        self.tools = tool_registry
        self.max_steps = max_steps
        self.verbose = verbose
        self.dry_run = dry_run
        self.memory = memory
        self.skill_manager = skill_manager
        self.state = AgentState.IDLE
        self.history: list[Message] = []
        self._session_tools: list[str] = []   # 本轮执行过的工具名
        self._session_file: str = ""           # 本轮操作的文件路径
        self._retry_counts: dict[str, int] = {}  # 工具重试计数器

        # 构建基础系统提示词（无技能上下文，启动时用）
        self._build_system_prompt()

    def _build_system_prompt(self, skills_context: str = ""):
        """构建并设置系统提示词"""
        tool_desc = self.tools.describe()
        memory_context = self.memory.get_context_summary() if self.memory else ""
        system_msg = Message(
            role=Role.SYSTEM,
            content=build_system_prompt(tool_desc, memory_context, skills_context),
        )
        # 替换或添加系统消息
        if self.history and self.history[0].role == Role.SYSTEM:
            self.history[0] = system_msg
        else:
            self.history.insert(0, system_msg)

    def run(self, user_input: str) -> str:
        """
        执行一次完整的 Agent 循环。

        Args:
            user_input: 用户的自然语言指令

        Returns:
            Agent 的最终回答文本
        """
        # 根据用户输入匹配 Skills，动态重建系统提示词
        if self.skill_manager:
            matched = self.skill_manager.match(user_input)
            skills_ctx = self.skill_manager.build_skills_context(matched)
            self._build_system_prompt(skills_ctx)
            if self.verbose and matched:
                names = [s.name for s in matched]
                print(f"📚 已加载技能: {', '.join(names)}")

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

    # ─────────────────────────────────────────────
    # 错误分类器（三级分类）
    # ─────────────────────────────────────────────

    def _classify_error(
        self, name: str, error: Exception
    ) -> tuple[str, str, list[str]]:
        """
        对工具执行错误进行三级分类，并生成修正建议。

        Returns:
            (error_level, error_summary, suggestions)
            error_level: "transient" | "correctable" | "fatal"
        """
        error_str = str(error)
        error_type = type(error).__name__

        # ── Level 1: 可自动重试的临时性错误 ──
        transient_patterns = [
            "超时", "timeout", "Timeout",
            "RPC", "网络", "connection", "Connection",
            "-2147",            # COM HRESULT 错误码
            "繁忙", "busy",
            "429", "rate limit",  # API 限流
        ]
        if any(p in error_str for p in transient_patterns):
            return (
                "transient",
                f"临时性错误 ({error_type}): {error_str[:100]}",
                ["系统将自动重试，无需干预"],
            )

        # ── Level 2: LLM 可自修正的错误 ──

        # 2a. 文件路径问题
        if isinstance(error, (FileNotFoundError, OSError)) or re.search(
            r"不存在|找不到|No such file|无法打开", error_str
        ):
            return (
                "correctable",
                f"文件路径错误 ({error_type})",
                [
                    "检查文件路径拼写是否正确（注意中文路径和空格）",
                    "调用 recall_history 查看上次处理过的文件路径",
                    "向用户询问正确的文件路径",
                ],
            )

        # 2b. 参数类型/缺失问题
        if isinstance(error, (TypeError, KeyError)) or "参数" in error_str:
            return (
                "correctable",
                f"工具参数错误 ({error_type})",
                [
                    "检查参数名称和类型是否与工具定义一致",
                    "确认所有 required 参数均已提供",
                    "布尔参数请使用 true/false 而非字符串",
                ],
            )

        # 2c. Word COM 接口问题
        if re.search(r"COM|com_error|Word|word|pywintypes", error_str):
            return (
                "correctable",
                f"Word COM 接口错误 ({error_type})",
                [
                    "确认 Microsoft Word 是否已启动（工具需要 Word 进程）",
                    "检查目标文档是否已被其他程序打开或处于只读状态",
                    "可尝试先调用 read_document 验证文档是否可访问",
                ],
            )

        # 2d. 权限问题
        if isinstance(error, PermissionError) or "权限" in error_str:
            return (
                "correctable",
                f"权限不足 ({error_type})",
                [
                    "文件可能被其他程序占用，请关闭后重试",
                    "检查文件是否为只读属性",
                    "向用户说明需要关闭占用文件的程序",
                ],
            )

        # ── Level 3: 不可恢复的错误 ──
        return (
            "fatal",
            f"不可恢复的错误 ({error_type})",
            [
                "此错误可能是工具内部问题，无法通过修改参数解决",
                "请向用户说明具体错误信息，建议手动处理",
            ],
        )

    # ─────────────────────────────────────────────
    # 结构化 Observation 构造
    # ─────────────────────────────────────────────

    def _build_error_observation(
        self,
        name: str,
        arguments: dict,
        error: Exception,
        attempt: int,
        max_attempts: int,
    ) -> str:
        """
        构造引导 LLM 自修正的结构化 Observation。

        不同于直接传递 traceback，此方法提供：
        1. 简明的错误摘要（减少 token 浪费）
        2. 错误分类（让 LLM 知道这是什么性质的问题）
        3. 具体修正建议（引导下一步推理方向）
        4. 重试计数（防止无限循环）
        """
        level, summary, suggestions = self._classify_error(name, error)

        # 截取错误信息的关键部分（去除冗长 traceback）
        error_detail = str(error)[:200]

        parts = [
            f"⚠️ 工具 {name} 执行失败",
            f"错误分类: {summary}",
            f"错误详情: {error_detail}",
            f"调用参数: {', '.join(f'{k}={v!r}' for k, v in arguments.items())}",
            "",
            "修正建议:",
        ]
        for i, s in enumerate(suggestions, 1):
            parts.append(f"  {i}. {s}")

        parts.append(f"")
        if attempt >= max_attempts:
            parts.append(
                f"⛔ 已对此工具尝试 {attempt}/{max_attempts} 次，"
                f"请换一种策略或向用户说明情况，不要再重复相同的调用。"
            )
        else:
            parts.append(
                f"📌 当前尝试 {attempt}/{max_attempts} 次。"
                f"请先分析失败原因，修正参数后重新调用。"
            )

        return "\n".join(parts)

    # ─────────────────────────────────────────────
    # 增强版工具执行（含重试跟踪 + 自动重试）
    # ─────────────────────────────────────────────

    def _execute_tool(self, call_id: str, name: str, arguments: dict) -> ToolResult:
        """执行单个工具调用（支持错误分类 + 结构化恢复引导）"""
        tool = self.tools.get(name)
        if tool is None:
            available = ", ".join(t.name for t in self.tools.get_all_tools())
            error = (
                f"⚠️ 未找到工具 '{name}'。\n"
                f"可用工具: {available}\n"
                f"请检查工具名称拼写后重新调用。"
            )
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

        # 重试跟踪：基于工具名计数（同名工具连续失败才计数）
        max_attempts = 3
        self._retry_counts[name] = self._retry_counts.get(name, 0) + 1
        attempt = self._retry_counts[name]

        # 实际执行
        try:
            output = tool.execute(**arguments)
            if self.verbose:
                # 截断过长输出
                display = output[:300] + "..." if len(output) > 300 else output
                print(f"  ✅ 结果: {display}")

            # 执行成功 → 重置该工具的重试计数
            self._retry_counts[name] = 0

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
            level, summary, _ = self._classify_error(name, e)

            # Level 1 (临时性错误): 自动重试，不消耗 LLM 推理步骤
            if level == "transient" and attempt < max_attempts:
                wait_sec = 2 ** attempt  # 指数退避: 2s, 4s, 8s
                if self.verbose:
                    print(f"  ⏳ 临时性错误，{wait_sec}秒后自动重试 ({attempt}/{max_attempts})...")
                time.sleep(wait_sec)
                return self._execute_tool(call_id, name, arguments)

            # Level 2 & 3: 构造结构化 Observation 引导 LLM 自修正
            error_obs = self._build_error_observation(
                name, arguments, e, attempt, max_attempts
            )
            if self.verbose:
                print(f"  ❌ {summary}")
            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=error_obs,
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
        self._retry_counts = {}
