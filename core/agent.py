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
from core.logger import logger
from core.schema import AgentState, Message, Role, ToolResult
from tools.base import ToolRegistry

# Word 相关工具名集合（用于 L1 后校验：自动检查 close_word 是否被遗漏）
WORD_TOOLS = {
    "read_document", "inspect_document_format", "format_references",
    "ref_crossref", "fig_crossref", "fig_caption", "acronym_checker",
    "summarize_document", "analyze_document", "index_document",
}


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
        self._active_config: dict = {}           # 本轮 Skill config（会话级）
        self._token_warning = 6000   # Token 水位：触发规则压缩（4000→6000，避免过早压缩稀释 L1）
        self._token_critical = 8000  # Token 水位：触发 LLM 深度压缩

        # 构建基础系统提示词（无技能上下文，启动时用）
        self._build_system_prompt()

    def _build_system_prompt(self, skills_context: str = "",
                             recalled_context: str = ""):
        """
        构建系统提示词（静态根 + 动态叶分离，Prompt Cache 友好）。

        消息布局：
          Message[0]: 静态 System（L1 规则 + 工具 + 行为准则）→ 跨轮次不变，可被缓存
          Message[1]: 动态 System（技能 + 记忆）→ 每轮变化，不影响 [0] 的缓存命中
          Message[2..]: User / Assistant / Tool 对话历史

        💡 设计原理：
          大模型 API 的 Prompt Cache 匹配 Token 序列的最长公共前缀。
          只要 Message[0] 内容不变，前缀就能命中缓存，获得 50%~90% 的降价。
        """
        from core.prompt import build_static_system_prompt, build_dynamic_context

        tool_desc = self.tools.describe()
        memory_context = (
            self.memory.get_context_summary(recalled_context)
            if self.memory else ""
        )

        # ── 静态根（跨轮次不变 → 缓存命中）──
        static_content = build_static_system_prompt(tool_desc)
        static_msg = Message(role=Role.SYSTEM, content=static_content)

        # ── 动态叶（每轮可变 → 独立消息，不污染前缀）──
        dynamic_content = build_dynamic_context(skills_context, memory_context)

        # 清除旧的 system 消息（可能有 1~2 条）
        while self.history and self.history[0].role == Role.SYSTEM:
            self.history.pop(0)

        # 按顺序插入：[0] 静态根, [1] 动态叶（如果有）
        if dynamic_content.strip():
            dynamic_msg = Message(role=Role.SYSTEM, content=dynamic_content)
            self.history.insert(0, dynamic_msg)
        self.history.insert(0, static_msg)

    def run(self, user_input: str) -> str:
        """
        执行一次完整的 Agent 循环。

        Args:
            user_input: 用户的自然语言指令

        Returns:
            Agent 的最终回答文本
        """
        # ── 会话级重置：防止跨轮次状态累积 ──
        self._session_tools = []
        self._session_file = ""
        self._retry_counts = {}
        self._active_config = {}

        # ── 向量记忆召回（RAG 式）──
        recalled = ""
        if self.memory:
            recalled = self.memory.recall_relevant(user_input)
            if self.verbose and recalled:
                logger.info("📌 已召回 %d 条相关历史", recalled.count('相关度'))

        # 根据用户输入匹配 Skills，动态重建系统提示词 + 提取 Config
        skills_ctx = ""
        if self.skill_manager:
            matched = self.skill_manager.match(user_input)
            skills_ctx = self.skill_manager.build_skills_context(matched)
            # 合并匹配到的 Skill 的 config 块（高优先级覆盖低优先级）
            self._active_config = self.skill_manager.get_active_config(matched)
            if self.verbose and matched:
                names = [s.name for s in matched]
                logger.info("📚 已加载技能: %s", ', '.join(names))
                if self._active_config:
                    logger.info("⚙️ 已注入 Skill Config: %s",
                                list(self._active_config.keys()))

        # 重建系统提示词（含召回的历史 + 技能）
        self._build_system_prompt(skills_ctx, recalled)

        # ── 策略一：三明治注入 ──
        # 将 L1 规则追加到用户消息末尾（紧贴 LLM 生成起始点）
        # 利用 LLM 近因效应（Recency Bias）最大化规则服从度
        from core.prompt import build_l1_user_suffix
        l1_suffix = build_l1_user_suffix(user_input)
        augmented_input = user_input + l1_suffix if l1_suffix else user_input

        # 添加用户消息（含 L1 后缀）
        self.history.append(Message(role=Role.USER, content=augmented_input))
        self.state = AgentState.THINKING

        if self.verbose:
            logger.info("\n" + "=" * 60)
            logger.info("🧠 Agent 收到指令: %s", user_input)
            logger.info("=" * 60)

        openai_tools = self.tools.to_openai_tools()

        for step in range(1, self.max_steps + 1):
            if self.verbose:
                logger.info("\n--- 第 %d/%d 步 ---", step, self.max_steps)

            # ── Token 水位线压缩：防止上下文爆炸 ──
            if self._estimate_tokens() > self._token_warning:
                self._compress_history()

            # 调用 LLM
            self.state = AgentState.THINKING
            try:
                response = self.llm.chat(self.history, tools=openai_tools)
            except Exception as e:
                error_msg = f"❌ LLM 调用失败: {e}"
                if self.verbose:
                    logger.error(error_msg)
                    traceback.print_exc()
                self.state = AgentState.ERROR
                return error_msg

            # 将 LLM 回复加入历史
            self.history.append(response)

            # 情况1: LLM 返回纯文本（没有工具调用） → 任务完成
            if not response.tool_calls:
                self.state = AgentState.FINISHED
                final_answer = response.content or "(Agent 没有给出回答)"

                # ── L1 规则后校验：自动检测并修正违规行为 ──
                l1_check = self._post_validate_l1()
                if l1_check:
                    final_answer += f"\n\n{l1_check}"
                    if self.verbose:
                        logger.info("🛡️ L1 后校验触发: %s", l1_check)

                if self.verbose:
                    logger.info("\n✅ Agent 回答:\n%s", final_answer)
                # 自动保存本轮操作到记忆（含向量存储）
                self._save_session(user_input, final_answer)
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
            logger.warning(timeout_msg)
        return timeout_msg

    # ─────────────────────────────────────────────
    # Hook 2: Token 水位线驱动的上下文压缩
    # ─────────────────────────────────────────────

    def _estimate_tokens(self) -> int:
        """
        估算当前 history 的 Token 数（中英文混合近似）。

        规则: len(text) // 2 对中英混合文本误差 ±20%。
        不引入 tiktoken 依赖，保持零额外依赖。
        """
        total = 0
        for msg in self.history:
            text = msg.content or ""
            total += len(text) // 2
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += len(str(tc.arguments)) // 2
        return total

    def _compress_history(self):
        """
        Token 水位线驱动的两级压缩。

        Tier 1 (token < _token_critical): 纯规则截取（零 LLM 成本）
        Tier 2 (token ≥ _token_critical): 调 LLM 做智能摘要

        压缩前: [System, User, LLM₁, Tool₁, ..., LLM₇, Tool₇]
        压缩后: [System, User, Scratchpad, LLM₆, Tool₆, LLM₇, Tool₇]
        """
        tokens = self._estimate_tokens()

        if tokens < self._token_warning:
            return

        keep_head = 2  # System + User
        keep_tail = 4  # 最近 2 轮工具交互

        if len(self.history) <= keep_head + keep_tail:
            return

        middle = self.history[keep_head:-keep_tail]
        if not middle:
            return

        if tokens < self._token_critical:
            # ── Tier 1: 规则压缩（零成本）──
            tool_summaries = []
            for msg in middle:
                if msg.role == Role.TOOL:
                    first_line = (msg.content or "").split("\n")[0][:80]
                    tool_name = msg.name or "unknown"
                    tool_summaries.append(f"{tool_name}: {first_line}")

            scratchpad_text = (
                "[已完成步骤] " + "; ".join(tool_summaries)
                if tool_summaries
                else "[已完成若干步骤]"
            )
        else:
            # ── Tier 2: LLM 深度压缩 ──
            middle_text = "\n".join(
                f"[{msg.role.value}] {(msg.content or '')[:200]}"
                for msg in middle
            )
            try:
                compress_msgs = [
                    Message(
                        role=Role.SYSTEM,
                        content=(
                            "将以下 Agent 执行过程压缩为 3 句话，"
                            "保留关键操作和结论，去除冗余细节。"
                        ),
                    ),
                    Message(
                        role=Role.USER,
                        content=middle_text[:2000],
                    ),
                ]
                resp = self.llm.chat(compress_msgs)
                scratchpad_text = (
                    f"[压缩摘要] {resp.content or '(压缩失败)'}"
                )
                if self.verbose:
                    logger.debug(
                        "  📋 Tier2 LLM 深度压缩: %d Token → ~%d Token",
                        tokens,
                        self._estimate_tokens(),
                    )
            except Exception:
                # LLM 失败时回退到规则压缩
                scratchpad_text = "[已完成若干步骤，因上下文过长已压缩]"

        scratchpad_msg = Message(
            role=Role.ASSISTANT,
            content=scratchpad_text,
        )

        old_len = len(self.history)
        self.history = (
            self.history[:keep_head]
            + [scratchpad_msg]
            + self.history[-keep_tail:]
        )

        if self.verbose:
            logger.debug(
                "  📋 Token 水位压缩: %d 条消息 → %d 条 (≈%d tokens)",
                old_len, len(self.history), self._estimate_tokens(),
            )

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
                logger.error("  ❌ %s", error)
            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=error,
                success=False,
            )

        if self.verbose:
            args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
            logger.info("  🔧 调用工具: %s(%s)", name, args_str)

        # Dry-run 模式：不实际执行
        if self.dry_run:
            output = f"[DRY-RUN] 将调用 {name}，参数: {arguments}"
            if self.verbose:
                logger.info("  🏜️ %s", output)
            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=output,
                success=True,
            )

        # 重试跟踪：基于工具名计数（同名工具连续失败才计数）
        max_attempts = 3
        attempt = self._retry_counts.get(name, 0) + 1
        self._retry_counts[name] = attempt

        # ── Skill Config 注入：将 config 中该工具对应的参数作为默认值注入 ──
        # LLM 显式传递的参数优先级更高，不会被 config 覆盖
        arguments = self._inject_skill_config(name, arguments)

        # 实际执行
        try:
            output = tool.execute(**arguments)
            if self.verbose:
                # 截断过长输出
                display = output[:300] + "..." if len(output) > 300 else output
                logger.info("  ✅ 结果: %s", display)

            # 执行成功 → 重置该工具的重试计数
            self._retry_counts[name] = 0

            # 记录工具执行（供记忆系统使用）
            self._session_tools.append(name)
            if "file_path" in arguments and not self._session_file:
                self._session_file = arguments["file_path"]

            # ── L2 效用反馈：工具成功 → 奖励召回的 L2 记忆 ──
            if self.memory:
                self.memory.reward_recalled_memories(delta=0.1)

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
                    logger.warning("  ⏳ 临时性错误，%d秒后自动重试 (%d/%d)...", wait_sec, attempt, max_attempts)
                time.sleep(wait_sec)
                return self._execute_tool(call_id, name, arguments)

            # Level 2 & 3: 构造结构化 Observation 引导 LLM 自修正
            error_obs = self._build_error_observation(
                name, arguments, e, attempt, max_attempts
            )
            if self.verbose:
                logger.error("  ❌ %s", summary)

            # ── L2 效用反馈：工具失败 → 轻度惩罚召回的 L2 记忆 ──
            if self.memory:
                self.memory.penalize_recalled_memories(delta=0.2)

            return ToolResult(
                tool_call_id=call_id,
                name=name,
                output=error_obs,
                success=False,
            )

    # ─────────────────────────────────────────────
    # L1 核心规则后校验器
    # ─────────────────────────────────────────────

    def _post_validate_l1(self) -> str:
        """
        L1 规则后校验：在 Agent 生成最终回答后，检查是否违反了核心规则。

        与 prompt 注入不同，这是代码级别的硬保障：
        - 不依赖 LLM 是否"注意到"了规则
        - 直接检查 _session_tools 里的实际行为
        - 检测到违规时自动执行修正动作

        Returns:
            违规报告文本，如果没有违规则返回空字符串。
        """
        if not self._session_tools:
            return ""

        try:
            from tools.learned_rules import _load_rules
            rules = _load_rules()
        except Exception:
            return ""

        if not rules:
            return ""

        used_tools = set(self._session_tools)
        violations = []

        for rule in rules:
            rule_text = rule["rule"].lower()

            # ── 模式匹配："Word进程" + "关闭" → 检查 close_word ──
            if ("word" in rule_text and ("关闭" in rule_text or "close" in rule_text)):
                word_used = used_tools & WORD_TOOLS
                if word_used and "close_word" not in used_tools:
                    # 自动修正：调用 close_word
                    tool = self.tools.get("close_word")
                    if tool:
                        try:
                            tool.execute()
                            self._session_tools.append("close_word")
                            violations.append(
                                f"⚠️ [L1 后校验] 检测到使用了 Word 工具 "
                                f"({', '.join(word_used)}) 但未关闭 Word 进程。"
                                f"已自动执行 close_word。"
                            )
                            if self.verbose:
                                logger.warning(
                                    "  🛡️ L1 后校验：自动关闭 Word 进程"
                                )
                        except Exception as e:
                            violations.append(
                                f"⚠️ [L1 后校验] 检测到违规：使用了 Word 工具但未关闭。"
                                f"自动修正失败: {e}"
                            )

            # ── 未来可扩展更多规则模式 ──
            # elif "其他关键词" in rule_text:
            #     ...

        return "\n".join(violations)

    def _save_session(self, user_input: str = "", summary: str = ""):
        """将本轮操作记录保存到记忆（含向量存储）"""
        if self.memory and self._session_tools:
            file_path = self._session_file or "unknown"
            self.memory.add_session(
                file_path=file_path,
                actions=self._session_tools,
                summary=summary[:200],  # 截断过长摘要
            )

        # 向量记忆：存入本轮 Q+A 摘要
        if self.memory and user_input:
            self.memory.add_to_vector(user_input, summary)

    # ─────────────────────────────────────────────
    # Skill Config → 工具参数注入
    # ─────────────────────────────────────────────

    # 工具名 → config 中对应的参数键 映射表
    # 定义了哪些 config 键应该注入到哪个工具
    _TOOL_CONFIG_MAP = {
        "inspect_document_format": ["format_rules"],
        "analyze_document": ["acronym_whitelist", "pipeline_order"],
    }

    def _inject_skill_config(self, tool_name: str, arguments: dict) -> dict:
        """
        将 Skill Config 中的相关参数注入到工具调用中。

        注入原则：
          - config 参数作为默认值（优先级低于 LLM 显式传参）
          - 只注入 _TOOL_CONFIG_MAP 中声明的参数
          - LLM 已经传递的参数不会被覆盖

        Args:
            tool_name: 工具名称
            arguments: LLM 传递的原始参数

        Returns:
            注入 config 后的参数字典
        """
        if not self._active_config:
            return arguments

        config_keys = self._TOOL_CONFIG_MAP.get(tool_name)
        if not config_keys:
            return arguments

        injected = dict(arguments)  # 浅拷贝，不修改原始参数
        for key in config_keys:
            if key not in injected and key in self._active_config:
                value = self._active_config[key]
                if value is not None:  # null 表示显式跳过
                    injected[key] = value
                    if self.verbose:
                        logger.debug("  ⚙️ Skill Config 注入: %s.%s",
                                     tool_name, key)

        return injected

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
        self._active_config = {}
