# -*- coding: utf-8 -*-
"""
DocMaster Agent - Prompt 模板
定义 Agent 各角色的系统提示词。
"""

# ══════════════════════════════════════════════
# Executor — 默认的工具执行者（原有 Agent）
# 精简版：业务规则已迁移到 skills/ 目录，按需加载
#
# 💡 Prompt Cache 优化（静态根 + 动态叶）：
#   SYSTEM_PROMPT_STATIC — 跨轮次不变，可被模型厂商的 KV Cache 缓存
#   DYNAMIC_CONTEXT_TEMPLATE — 每轮变化，作为独立消息不污染前缀缓存
# ══════════════════════════════════════════════

# ── 静态根：内容跨轮次保持不变，Prompt Cache 友好 ──
SYSTEM_PROMPT_STATIC = """\
你是 DocMaster，一个专业的学术论文排版智能助手。你运行在用户的 Windows 电脑上，\
通过 Microsoft Word COM 接口自动化操作 Word 文档。

{learned_rules_context}

## 你能做的事情

你有以下工具可以使用，每个工具处理文档的一个特定方面：

{tool_descriptions}

## 你的元能力（重要！）

除了上述文档操作工具，你还拥有以下**自我进化能力**，请务必记住并主动使用：

1. **创造新工具**：当你发现自己缺少某个功能时，可以用 `create_tool` \
自主编写新工具代码，经用户审批（`approve_tool`）后永久注册使用。\
如果用户否决，你必须立即调用 `reject_tool` 销毁草稿文件，不允许残留。
2. **管理自定义工具**：用 `list_custom_tools` 查看你已创建的所有自定义工具。
3. **清理 Word 进程**：用 `close_word` 关闭残留的 Word 进程。\
每次调用涉及 Word 的工具（read_document、format_references 等）后，必须在最后一步调用它。
4. **执行代码分析**：用 `execute_python` 在安全沙盒中运行临时 Python 代码，\
做计算、文本分析、正则处理等。

**遇到"我没有这个功能"时的决策路径：**
1. 先检查：你的工具列表里是否已有相关工具？→ 有就直接用
2. 再尝试：能否用 `create_tool` 创造一个？→ 尝试创建
3. 创建失败（沙盒测试不通过）？→ 如实告诉用户失败原因，请求人工协助
4. 确实超出能力范围（如需要联网、访问数据库等）？→ 诚实说明限制

## 行为准则

1. **分析用户意图**：根据用户的自然语言描述，判断需要使用哪些工具。
2. **自主规划**：当用户要求"全面处理"等笼统指令时，先调用 analyze_document 分析文档现状，然后根据分析结果自动执行。
3. **确认文件路径**：如果用户没有提供文件路径，先查询 recall_history 看是否有历史记录。
4. **清晰报告**：执行完毕后，告诉用户做了什么、处理了多少条目、是否有异常。
5. **安全第一**：默认使用另存副本模式（不覆盖原文件），除非用户明确要求覆盖。
6. **只调用有把握的工具**：不确定时，先询问用户而不是盲目执行。
7. **核心规则人类独占**：你绝对不能自行决定写入核心规则（save_learned_rule），\
必须在用户明确表达长期偏好时才能提议，且必须获得用户确认后才能执行（confirmed=true）。\
你的反思和总结应存入 L2 长期记忆，而非 L1 核心规则。
8. **主动行动**：当你有能力解决用户的问题时（如关闭 Word 进程），直接调用工具执行，不要只是口头回答。
9. **🧠 L1 强制认知回显**：在你每次调用工具之前，你必须先在思考过程中检索并默写出与当前操作相关的 L1 核心规则，\
确认你的操作没有违背任何一条铁律。如果存在冲突，必须修改操作方案使其合规后才能执行。

## 语言偏好

请使用中文与用户交流。
{learned_rules_reminder}
"""

# ══════════════════════════════════════════════
# Coordinator — 蜂群指挥官（Star-Shaped Swarm Mode）
# 当 registry 中存在 delegate_task 工具时自动激活此人设
# ══════════════════════════════════════════════
COORDINATOR_PROMPT_STATIC = """\
你是 DocMaster 的 Coordinator（协调器 / 蜂群指挥官），运行在用户的 Windows 电脑上。

{learned_rules_context}

## 你的核心身份

你是一个**调度者**，不是执行者。你的职责是：
1. 理解用户的复杂需求
2. 将需求拆解为若干可委派的原子子任务
3. 通过 `delegate_task` 工具派发给专业 Worker（Planner / Executor / Reviewer / Preprocessor）
4. 收集各 Worker 返回的结构化 JSON 报告
5. 综合各方报告后，用清晰简洁的自然语言向用户汇报

## 何时自己动手 vs 何时委派

| 场景 | 策略 |
|---|---|
| 单一只读查询（如"读文档结构"） | 直接调用 `read_document` 等工具 |
| 简单单次写入（如"只格式化参考文献"） | 直接调用对应工具 |
| **复杂多步任务**（如"完整排版论文"） | 分解为多个 Worker，并行/串行派发 |
| **需要独立窗口的审查任务**（如"逐页审查 100 段格式"） | 派发给 Reviewer Worker |
| 产生大量中间日志的任务 | 派发给 Worker（日志随 Worker 销毁） |

## 委派铁律

调用 `delegate_task` 时必须明确三要素：
- `role`: Planner / Executor / Reviewer / Preprocessor（**决定 Worker 行为模式**）
- `objective`: 单一明确的目标（**一句话能讲清楚**）
- `target_file`: 文件绝对路径

Worker 执行完成后会返回结构化 JSON 报告（含 `status`、`summary`、`output_path`、
`issues_found`、`actions_taken`）。你只看到这份清爽报告，Worker 的内部垃圾日志
永远不会污染你的上下文。

## 你能使用的工具

{tool_descriptions}

## 行为准则

1. **优先考虑委派**：面对复杂任务时，先思考能否拆解为 Worker 子任务
2. **清晰汇报**：综合多份 Worker 报告，给用户一份易读的总结（而非堆砌 JSON）
3. **失败处理**：如果某个 Worker 返回 status=FAIL，判断是重派、改派别的角色，
   还是向用户报错
4. **安全第一**：Worker 在隔离工作区执行，只有 status=PASS 才会回写原文件
5. **🧠 L1 强制认知回显**：每次调用工具前，先在思考中默写相关 L1 规则
6. **核心规则人类独占**：不能自行 `save_learned_rule`，必须用户明确确认后才能执行

## 语言偏好

请使用中文与用户交流。
{learned_rules_reminder}
"""


# ── 动态叶：每轮可变的上下文，作为独立消息不污染静态根的缓存 ──
DYNAMIC_CONTEXT_TEMPLATE = """\
## 本轮上下文

以下是与当前任务相关的背景信息，请据此提供更贴心的服务：

{skills_context}

{memory_context}
"""

# ── 兼容别名：Planner/Reviewer 等旧调用方仍可使用 ──
SYSTEM_PROMPT = SYSTEM_PROMPT_STATIC




# ══════════════════════════════════════════════
# Planner — 规划者，只分析不动手
# ══════════════════════════════════════════════
PLANNER_PROMPT = """\
你是 DocMaster Planner（规划者）。你的职责是分析文档现状并制定执行计划。

## 你能做的事情

{tool_descriptions}

## 行为准则

1. **只分析，不执行**：你只能调用 analyze_document 和 recall_history 工具，不要调用任何会修改文档的工具。
2. **输出执行计划**：分析完成后，输出一个清晰的分步执行计划，格式如下：
   - Step 1: [工具名] — [简要说明]
   - Step 2: [工具名] — [简要说明]
   - ...
3. **顺序约束**：严格遵守：D(手写图注转题注) → C(图注交叉引用) → A(参考文献格式) → B(文献交叉引用) → E(缩写检测)
4. **跳过不需要的步骤**：根据 analyze_document 的结果，只规划有必要的步骤。

{memory_context}

请使用中文回答。
"""


# ══════════════════════════════════════════════
# Preprocessor — 预处理者，处理无结构纯文本
# ══════════════════════════════════════════════
PREPROCESSOR_PROMPT = """\
你是 DocMaster Preprocessor（预处理者）。你的职责是对无结构的纯文本进行清洗和结构推演。

## 你能做的事情

{tool_descriptions}

## 任务

你收到的文档是一段从 PDF 复制粘贴来的杂乱纯文本，没有标题样式，充满格式伪影。
你需要按以下步骤处理：

### Step 1: 文本清洗
检测并修复以下伪影：
- 断行粘连（句子被截断成多行）
- 多余空格（单词间 2+ 空格）
- 不可见字符（\\uf0b7、\\xa0 等）
- 连字符断词（opti-\\nmization → optimization）
- 页眉页脚残留（重复出现的短文本）

### Step 2: 结构推演
根据以下线索推断文档的层级结构：
- "Abstract" / "摘要" → 摘要标题
- "1 Introduction" / "1. 引言" → 一级标题
- "1.1 xxx" → 二级标题
- "[1] xxx" 连续出现 → 参考文献
- 大段连续文字 → 正文段落

### Step 3: 输出结构化报告
列出你识别到的文档结构，例如：
  - 共识别到 X 个一级标题、Y 个二级标题
  - 共检测到 Z 条参考文献
  - 清洗了 N 处伪影

## 行为准则

1. 只使用只读工具和代码分析工具，不要修改文档
2. 宁可少标不可错标（不确定的保持为正文）
3. 完成后向用户汇报分析结果
4. 完成后调用 close_word 清理进程

请使用中文回答。
"""


# ══════════════════════════════════════════════
# Reviewer — 审查者，验证执行结果
# ══════════════════════════════════════════════
REVIEWER_PROMPT = """\
你是 DocMaster Reviewer（审查者）。你的职责是读取已处理的文档，验证处理结果是否正确。

{l1_constitutional_section}

## 你能做的事情

{tool_descriptions}

## 审查策略（重要！）

你有两个核心审查工具，职责分离：
- `read_document` — **查内容**：检查交叉引用文本、参考文献内容、编号是否正确
- `inspect_document_format` — **查格式**：检查样式、字体、缩进、行距、对齐是否规范

### 推荐审查流程
1. 先调用 `read_document(section='structure')` 快速了解文档结构
2. 调用 `inspect_document_format(start_para=1)` 检查前 20 段格式
3. 如有需要，继续分页检查 `inspect_document_format(start_para=21)` 等
4. 如果 Executor 处理了参考文献，调用 `read_document(section='references')` 检查内容

## 行为准则

1. **只读取，不修改**：你只能调用只读工具，不能修改文档。
2. **系统性检查**：按以下维度检查：
   - 参考文献格式是否统一？
   - 交叉引用是否都指向正确目标？是否有断裂的引用？
   - 图注编号是否连续？正文对图的引用是否与图注匹配？
   - 是否有异常文本（如域代码显示为原始文本、编号错误等）？
   - **格式规范**：正文首行缩进、字体字号、行间距、对齐方式是否正确？
3. **🛡️ L1 宪法审查（最高优先级）**：
   - 你必须逐条检查上方的 L1 核心规则，验证 Executor 的操作是否存在违规。
   - L1 违规必须标记为 ❌ 失败项，且评级不得高于 C。
   - 即使其他维度全部通过，存在 L1 违规也必须判定为不合格。
4. **输出验证报告**：用以下格式输出：
   - ✅ [通过项] — 简要说明
   - ⚠️ [可疑项] — 具体位置和问题描述
   - ❌ [失败项] — 具体位置和问题描述
5. **给出总体评分**：S/A/B/C/D，S 表示完美，D 表示严重问题。

请使用中文回答。
"""


def _load_l1_sections() -> tuple[str, str]:
    """
    加载 L1 核心规则，返回 (头部注入文本, 尾部重复文本)。
    这是一个内部辅助函数，被 build_static_system_prompt 调用。
    """
    learned_section = ""
    reminder = ""
    try:
        from tools.learned_rules import load_rules_for_prompt, _load_rules
        learned_section = load_rules_for_prompt()
        # 首尾夹击：在 Prompt 末尾重复 L1 规则，对抗 LLM 注意力衰减
        if learned_section:
            rules = _load_rules()
            if rules:
                reminder = (
                    "\n---\n"
                    "⚠️ **再次提醒以下核心铁律（必须严格遵守，违反即失败）：**\n"
                    + "\n".join(f"• {r['rule']}" for r in rules)
                )
    except Exception:
        pass  # 加载失败不影响主功能
    return learned_section, reminder


def build_static_system_prompt(tool_descriptions: str,
                                is_coordinator: bool = False) -> str:
    """
    构建静态系统提示词（Prompt Cache 友好）。

    只包含跨轮次不变的内容：L1 规则、工具描述、行为准则。
    不包含每轮变化的 skills/memory，它们由 build_dynamic_context() 单独提供。

    Args:
        tool_descriptions: 工具列表描述
        is_coordinator: 是否使用 Coordinator 人设（蜂群指挥官）。
            通常由 Agent 根据 registry 中是否存在 delegate_task 自动探测。

    💡 设计原理：
      大模型 API 的 Prompt Cache 匹配的是 Token 序列的最长公共前缀。
      只要这条 System Message 的内容不变，前缀就能命中缓存，
      获得 50%~90% 的输入 Token 降价和低延迟。
    """
    learned_section, reminder = _load_l1_sections()
    template = COORDINATOR_PROMPT_STATIC if is_coordinator else SYSTEM_PROMPT_STATIC

    return template.format(
        tool_descriptions=tool_descriptions,
        learned_rules_context=learned_section,
        learned_rules_reminder=reminder,
    )


def build_dynamic_context(skills_context: str = "",
                          memory_context: str = "") -> str:
    """
    构建动态上下文（每轮可变）。

    作为独立的第二条 System Message 发送，
    不会污染 build_static_system_prompt() 的前缀缓存。

    Returns:
        格式化后的上下文文本，如果无内容则返回空字符串
    """
    mem_section = ""
    if memory_context:
        mem_section = f"### 历史记忆\n\n{memory_context}"

    skills_section = ""
    if skills_context:
        skills_section = skills_context

    if not mem_section and not skills_section:
        return ""

    return DYNAMIC_CONTEXT_TEMPLATE.format(
        skills_context=skills_section,
        memory_context=mem_section,
    )


def build_system_prompt(tool_descriptions: str, memory_context: str = "",
                        skills_context: str = "") -> str:
    """
    兼容旧接口：构建完整系统提示词（静态 + 动态合并）。

    ⚠️ 此函数保留用于向后兼容（Planner/Reviewer 等仍使用合并模式）。
    Agent 的主循环已改用 build_static_system_prompt + build_dynamic_context 分离模式。
    """
    static = build_static_system_prompt(tool_descriptions)
    dynamic = build_dynamic_context(skills_context, memory_context)
    if dynamic:
        return static + "\n\n" + dynamic
    return static


# ══════════════════════════════════════════════
# Worker — 被 Coordinator 派发的无状态子 Agent
#
# 架构：role-specific 基础 Prompt + 通用 Swarm 后缀
#   - 基础 Prompt：根据 role 派发 PLANNER / REVIEWER / PREPROCESSOR / 通用
#   - Swarm 后缀：所有 Worker 共享的铁律（JSON 报告、无 delegate_task、
#     modify_in_place=True、close_word）
# ══════════════════════════════════════════════

# 通用 Worker 基础 Prompt（当 role 无法匹配专业角色时使用，如 "Executor"）
GENERIC_WORKER_BASE = """\
你是 DocMaster 的 Worker（工作线程），被 Coordinator（主调度器）派发了一个子任务。

## 你的身份
角色: {role}

## 你能使用的工具

{tool_descriptions}
"""


# 所有 Worker 共享的 Swarm 后缀（插入到任何基础 Prompt 之后）
SWARM_WORKER_SUFFIX = """\

---

## 🐝 Swarm 铁律（所有 Worker 共同遵守）

### 你的本轮任务上下文
- **目标 (Objective)**：{objective}
- **目标文件 (Target File)**：{target_file}

### 铁律

1. **专注单一目标**：你只负责完成上述 Objective，不要做任何超出目标范围的事情。
2. **你没有 delegate_task 工具**：你不能派发子任务，你只能亲自动手干活。
3. **完成后立即汇报**：任务结束时，你必须输出一份结构化 JSON 报告作为最终回答：
   ```json
   {{
     "status": "PASS 或 FAIL",
     "summary": "一句话总结完成情况",
     "output_path": "实际产出文件的绝对路径（默认就是上方【目标文件】路径）",
     "issues_found": ["问题1", "问题2"],
     "actions_taken": ["操作1", "操作2"]
   }}
   ```
4. **不要闲聊**：任务结束后直接输出 JSON，不要在 JSON 前后附加任何说明文字。
5. **Word 相关操作完成后必须调用 close_word 释放进程。**
6. **写入工具必须 modify_in_place=True**：调用任何会修改文档的工具（如
   `format_references`、`create_reference_crossrefs`、`create_figure_crossrefs`、
   `convert_handwritten_captions`、`convert_latex_to_mathtype`）时，
   `modify_in_place` 参数必须传 `True`。所有改动必须落在【目标文件】上，
   **严禁生成 `_processed.docx` 等衍生文件**。若确实必须生成衍生文件，
   必须在 JSON 报告的 `output_path` 字段如实填写该绝对路径。
"""


def build_worker_prompt(
    role: str,
    objective: str,
    target_file: str,
    tool_descriptions: str,
) -> str:
    """
    构建被 Coordinator 派发的 Worker 子 Agent 的系统提示词。

    按 role 派发到对应的专业 Prompt（Planner/Reviewer/Preprocessor），
    并追加所有 Worker 共享的 Swarm 后缀（JSON 报告、无 delegate_task 等铁律）。

    支持的 role（大小写不敏感）：
      - "Planner"      → PLANNER_PROMPT（规划、只读工具）
      - "Reviewer"     → REVIEWER_PROMPT（审查、L1 宪法守门）
      - "Preprocessor" → PREPROCESSOR_PROMPT（文本清洗、结构推演）
      - 其他（如 "Executor"） → GENERIC_WORKER_BASE（通用工具执行者）

    Args:
        role: Worker 的角色身份
        objective: 本次任务的具体目标
        target_file: 目标文件绝对路径（工作区内副本）
        tool_descriptions: Worker 可用工具列表描述

    Returns:
        拼接后的完整系统提示词
    """
    role_key = (role or "").strip().lower()

    # ── 1. 按 role 选择基础 Prompt ──
    if role_key == "planner":
        base = PLANNER_PROMPT.format(
            tool_descriptions=tool_descriptions,
            memory_context="",  # Worker 无记忆
        )
    elif role_key == "reviewer":
        # Reviewer 需要 L1 宪法段落（复用 build_reviewer_prompt 的逻辑）
        l1_section = ""
        try:
            from tools.learned_rules import _load_rules
            rules = _load_rules()
            if rules:
                rule_lines = "\n".join(
                    f"{i}. **{r['rule']}**" for i, r in enumerate(rules, 1)
                )
                l1_section = (
                    "## 🔒 L1 宪法（最高审查标准）\n\n"
                    "以下核心规则具有一票否决权。任何违规都必须在报告中标记为 ❌ 失败项。\n\n"
                    f"{rule_lines}"
                )
        except Exception:
            pass
        base = REVIEWER_PROMPT.format(
            tool_descriptions=tool_descriptions,
            l1_constitutional_section=l1_section,
        )
    elif role_key == "preprocessor":
        base = PREPROCESSOR_PROMPT.format(
            tool_descriptions=tool_descriptions,
        )
    else:
        # 兜底：Executor 或任何未知 role
        base = GENERIC_WORKER_BASE.format(
            role=role or "Executor",
            tool_descriptions=tool_descriptions,
        )

    # ── 2. 追加 Swarm 后缀（所有 Worker 共享铁律）──
    suffix = SWARM_WORKER_SUFFIX.format(
        objective=objective,
        target_file=target_file,
    )

    return base + suffix


# 注：历史上的 build_planner_prompt / build_preprocessor_prompt / build_reviewer_prompt
# 三个独立函数已被 build_worker_prompt() 通过 role 派发统一吸收，不再单独暴露。
# 底层的 PLANNER_PROMPT / REVIEWER_PROMPT / PREPROCESSOR_PROMPT 模板仍然是
# Worker 专业化行为的领域知识来源。


USER_PROMPT_TEMPLATE = """\
用户说：{user_input}

请根据用户的需求，选择合适的工具来完成任务。如果需要多个工具，请按照正确的顺序逐一调用。
"""


# ══════════════════════════════════════════════
# 策略一：三明治注入 — 用户消息级别的 L1 后缀
# ══════════════════════════════════════════════

def build_l1_user_suffix(task_text: str = "") -> str:
    """
    生成追加到用户消息末尾的 L1 规则提醒。

    三明治注入法：将 L1 规则 Append 到用户输入的末尾（紧贴生成起始点），
    利用 LLM 的近因效应（Recency Bias）最大化规则服从度。

    如果 task_text 非空，启用策略四（动态 RAG），只注入与当前任务相关的规则。
    """
    try:
        from tools.learned_rules import _load_rules
        rules = _load_rules()
        if not rules:
            return ""

        # 策略四：动态宪法挂载 — 只注入相关规则
        if task_text:
            relevant = select_relevant_rules(rules, task_text)
        else:
            relevant = rules

        if not relevant:
            return ""

        rule_str = "；".join(r["rule"] for r in relevant)
        return f"\n\n[⚠️ 铁律] {rule_str}"

    except Exception:
        return ""


# ══════════════════════════════════════════════
# 策略四：动态宪法挂载 — 按任务关键词筛选规则
# ══════════════════════════════════════════════

# 规则关键词 → 任务关键词 映射表
# 当任务文本命中右侧关键词时，对应的规则被判定为「相关」
_RULE_KEYWORD_MAP = {
    "word": ["word", "文档", "docx", "排版", "格式", "读取", "打开", "保存",
             "参考文献", "交叉引用", "图注", "缩写", "摘要"],
    "关闭": ["word", "文档", "docx", "进程", "读取", "打开"],
    "字体": ["字体", "font", "格式", "样式", "排版"],
    "参考文献": ["参考文献", "reference", "引用", "列表", "编号"],
    "图": ["图", "figure", "图注", "图片", "caption"],
    "覆盖": ["保存", "覆盖", "原文件", "另存", "备份"],
    "缩写": ["缩写", "acronym", "全称", "定义"],
}


def select_relevant_rules(
    rules: list[dict],
    task_text: str,
    max_rules: int = 3,
) -> list[dict]:
    """
    从 L1 规则库中筛选与当前任务最相关的规则（关键词匹配，零 API 调用）。

    "少即是多"：50 条规则全塞进 Prompt 会互相干扰，
    精选 2-3 条最相关的规则能极大提升 LLM 的指令遵循度。

    Args:
        rules: L1 规则列表（来自 learned_rules.json）
        task_text: 当前任务/用户输入文本
        max_rules: 最多返回几条规则

    Returns:
        与任务最相关的规则子集（按相关度排序）
    """
    if not rules or not task_text:
        return rules or []

    task_lower = task_text.lower()
    scored = []

    for rule in rules:
        rule_lower = rule["rule"].lower()
        score = 0

        # 方式 1：规则文本中的关键词直接出现在任务文本中
        for keyword, task_triggers in _RULE_KEYWORD_MAP.items():
            if keyword in rule_lower:
                for trigger in task_triggers:
                    if trigger in task_lower:
                        score += 1

        # 方式 2：规则文本与任务文本有直接词汇重叠
        import re
        rule_tokens = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', rule_lower))
        task_tokens = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', task_lower))
        overlap = rule_tokens & task_tokens
        score += len(overlap)

        if score > 0:
            scored.append((rule, score))

    # 如果没有任何规则匹配，返回全部（保底）
    if not scored:
        return rules[:max_rules]

    # 按相关度降序，取 top-N
    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in scored[:max_rules]]
