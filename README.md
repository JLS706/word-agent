# 🤖 DocMaster Agent

**学术论文排版 AI 智能助手** —— 通过自然语言指令驱动 Word 文档自动化处理。

基于 **ReAct (Reasoning + Acting)** 架构，参考 [OpenManus](https://github.com/FoundationAgents/OpenManus) 和 [smolagents](https://github.com/huggingface/smolagents) 设计理念打造的轻量级 AI Agent 项目。

## ✨ 功能

### 文档处理工具

| 工具 | 功能描述 |
|------|---------|
| `inspect_document_format` | 📐 **格式检查**（样式/字体/缩进/行距/对齐），内置学术规范自动诊断 |
| `format_references` | 参考文献格式修复（字体统一、Sentence Case、期刊名斜体） |
| `create_reference_crossrefs` | 文献交叉引用生成（[1][2] → 可跳转域代码） |
| `create_figure_crossrefs` | 图注交叉引用生成（图X.Y → 可跳转域代码） |
| `convert_handwritten_captions` | 手写图注转 Word 题注（支持自动编号） |
| `check_acronym_definitions` | 缩写定义检测（检测 MIMO 等缩写是否有全称） |
| `convert_latex_to_mathtype` | LaTeX 公式 → MathType 批量转换 |

### 文档分析工具

| 工具 | 功能描述 |
|------|---------|
| `read_document` | 读取文档内容（全文/标题结构/参考文献），只看内容不查格式 |
| `analyze_document` | 扫描文档现状（参考文献/图注/公式/缩写统计） |
| `summarize_document` | Map-Reduce 全文摘要（分块摘要 → 合成总结，覆盖全文） |
| `index_document` + `search_document` | RAG 向量检索（文档索引 → 语义搜索） |

### 高级能力

| 能力 | 描述 |
|------|---------|
| `execute_python` | 安全沙盒代码解释器（三层安全防护 + 进程级隔离） |
| `create_tool` / `approve_tool` | 🧬 Agent 自主创造新工具（编写 → 沙盒测试 → 审批注册） |
| `save/forget/list_learned_rules` | Agent 自学习系统（将经验存为规则，持续改进） |
| 三级分层记忆 | L1 核心规则 / L2 长期反思 / L3 短期对话，含召回率淘汰 + L2 融合节点 |
| Multi-Agent 流水线 | Planner → Executor → Reviewer 三角色协作 |
| 子任务反思 Hook | 子任务完成后自动提炼经验存入 L2 长期记忆 |
| Token 水位压缩 | 基于 Token 估算的两级上下文压缩（规则截取 / LLM 摘要） |
| Skill 插件系统 | 按需加载技能手册（关键词 + Embedding 双层匹配） |
| 多 Key 自动轮换 | API Key 失效时自动切换到下一个可用 Key |
| 结构化错误恢复 | 三级错误分类 + 自动重试 + 引导 LLM 自修正 |
| 回溯修正 (Backtracking) | 逐步执行 → 每步验证 → 失败时自动回溯 |
| Checkpoint 断点续传 | 流水线中途中断后可从断点恢复 |

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 LLM API

```bash
cp config/config.example.toml config/config.toml
```

编辑 `config/config.toml`，填入你的 API Key。支持多 Key 逗号分隔（自动 Failover）：

```toml
api_key = "key1, key2, key3"    # 失效自动切换到下一个
```

推荐使用免费方案：

| 方案 | 申请地址 | 免费额度 |
|------|---------|---------|
| **Google Gemini** | [aistudio.google.com](https://aistudio.google.com/apikey) | 每天 1500 次请求 |
| **智谱 GLM-4-Flash** | [open.bigmodel.cn](https://open.bigmodel.cn/) | 永久免费 |
| **硅基流动** | [siliconflow.cn](https://cloud.siliconflow.cn/) | 注册送 2000 万 Token |

### 3. 测试连接

```bash
python main.py --test
```

### 4. 开始使用

```bash
python main.py
```

然后输入自然语言指令，如：
- `帮我检查一下论文的格式有没有问题`
- `帮我格式化参考文献`
- `生成文献交叉引用`
- `检查一下缩写有没有定义`
- `总结一下这篇论文的主要内容`
- `pipeline C:\path\to\论文.docx`（Multi-Agent 全流程）

## 🏗️ 项目架构

```
agent/
├── config/
│   ├── config.example.toml     # 配置模板（多方案示例）
│   └── config.toml             # 实际配置（.gitignore）
├── core/
│   ├── schema.py               # 数据模型（Message, ToolCall, AgentState）
│   ├── llm.py                  # LLM 接口封装（多 Key 自动轮换）
│   ├── agent.py                # ReAct Agent 核心（Token 水位压缩 + 结构化错误恢复）
│   ├── prompt.py               # System Prompt 模板（Executor / Planner / Reviewer）
│   ├── memory.py               # 三级分层记忆（L1核心/L2长期/L3短期 + 融合节点）
│   ├── embeddings.py           # Embedding + 向量存储（纯 numpy 实现）
│   ├── multi_agent.py          # Multi-Agent 流水线（回溯修正 + 反思 Hook）
│   ├── skills.py               # Skill 插件管理器（关键词 + Embedding 双层匹配）
│   ├── sandbox.py              # 安全沙盒（三层防护 + Docker 隔离）
│   ├── checkpoint.py           # 断点续传状态管理
│   └── com_watchdog.py         # Word COM 进程隔离守护
├── tools/
│   ├── base.py                 # Tool 基类 + ToolRegistry
│   ├── doc_format_inspector.py # 📐 文档格式检查（样式/字体/缩进/行距诊断）
│   ├── doc_reader.py           # 文档内容读取
│   ├── doc_summarizer.py       # Map-Reduce 全文摘要
│   ├── rag.py                  # RAG 向量检索（索引 + 搜索）
│   ├── pipeline.py             # 文档分析（宏观扫描）
│   ├── ref_formatter.py        # 参考文献格式化
│   ├── ref_crossref.py         # 文献交叉引用
│   ├── fig_crossref.py         # 图注交叉引用
│   ├── fig_caption.py          # 手写图注转题注
│   ├── acronym_checker.py      # 缩写检测
│   ├── latex_converter.py      # LaTeX 转换
│   ├── code_interpreter.py     # 安全沙盒代码解释器
│   ├── learned_rules.py        # Agent 自学习规则
│   ├── tool_creator.py         # 🧬 动态工具创建引擎
│   ├── memory_tool.py          # 记忆查询/保存
│   └── word_cleanup.py         # Word 进程清理
├── skills/                     # Skill 插件目录（.md 格式，热加载）
│   ├── paper_formatting.md     # 论文排版标准流
│   ├── doc_summary.md          # 文档摘要技能
│   ├── rag_search.md           # RAG 搜索技能
│   └── data_analysis.md        # 数据分析技能
├── memory/                     # 持久化存储
│   ├── history.json            # 操作历史（最近50条）
│   ├── learned_rules.json      # L1 核心规则（永不过期）
│   └── memory_vectors.json     # L2 + L3 向量记忆（含召回追踪）
├── sandbox/                    # Docker 沙盒微服务
└── main.py                     # 入口
```

## 🧠 工作原理

### 单 Agent 模式（ReAct 循环）

```
用户: "帮我检查论文格式并处理参考文献"
  ↓
[Skill 匹配] "论文排版标准流" → 注入 System Prompt
  ↓
[记忆召回] 向量搜索相关历史 → 注入上下文
  ↓
Agent (Think): 先用 inspect_document_format 检查格式，再处理参考文献
  ↓
Agent (Act): inspect_document_format → 发现3处缩进问题
  ↓
Agent (Act): format_references → 修复25条参考文献
  ↓
Agent (Observe): 成功 → 继续 | 失败 → 结构化错误引导自修正
  ↓
Agent: "已完成！" → 保存 Q+A 到 L3 短期记忆
```

### Multi-Agent 流水线（含回溯修正 + 反思 Hook）

```
Phase 1 — Planner:  分析文档 → 制定执行计划 [Step1, Step2, ..., StepN]
  ↓
Phase 2 — Executor (逐步执行 + 回溯):
  Step 1 → 验证 ✅ → 💡 反思提炼经验 → 存入 L2 → Checkpoint
  Step 2 → 验证 ✅ → 💡 反思提炼经验 → 存入 L2 → Checkpoint
  Step 3 → 验证 ❌ → 关键错误? 🚨 汇报人类
                    → 普通错误? 🔄 重试 → 🧭 重规划 → ⏩ 跳过
  ↓
Phase 3 — Reviewer: 读取内容 + 检查格式 → 验证并输出报告（S/A/B/C/D 评分）
```

### 三级分层记忆 (Hierarchical Memory)

```
┌─────────────────────────────────────────────────────────────────┐
│  L1: Core Memory (核心规则库)                                    │
│  learned_rules.json                                             │
│  永不压缩 · 永不过期 · System Prompt 强制注入                      │
│  例: "工具执行后关闭Word进程"                                      │
├─────────────────────────────────────────────────────────────────┤
│  L2: Long-term RAG (长期向量记忆)                                 │
│  memory_vectors.json (type=reflection)                          │
│  来源: 子任务反思 / L3 晋升                                       │
│  FIFO 上限 50 条 · 冲突时 LLM 融合判断(REPLACE/MERGE)             │
│  例: "交叉引用断裂时，先转纯文本再挂书签；仍失败则删旧书签重建"        │
├─────────────────────────────────────────────────────────────────┤
│  L3: Short-term Conversation (短期对话记忆)                      │
│  memory_vectors.json (type=conversation)                        │
│  30天 TTL + 召回率末尾淘汰                                        │
│  eviction_score = recall_count × exp(-days_since_recall / 30)   │
│  recall_count ≥ 5 → 自动晋升到 L2                                │
└─────────────────────────────────────────────────────────────────┘
```

冲突消解规则（层感知）：

| 新记忆 \ 命中旧记忆 | L3 | L2 | L1 |
|---|---|---|---|
| **L3** | ✅ 同层替换 | ❌ 不动 L2 | ❌ 不动 L1 |
| **L2** | 🗑️ 清理冗余 L3 | 🔀 LLM 融合判断 | ❌ 不动 L1 |

### Token 水位线压缩 (Context Overflow Hook)

```
估算 Token ─── < 4000 ───→ 不压缩
    │
    ├── 4000~6000 ──→ Tier 1: 纯规则截取（零 LLM 成本）
    │
    └── ≥ 6000 ────→ Tier 2: LLM 智能摘要（~600 Token 成本）
```

### 格式感知架构（查格式 vs 查内容，职责分离）

```
                    ┌─ read_document ──→ 纯文本内容（适合摘要/搜索/问答）
用户文档 ──→ COM ──┤
                    └─ inspect_format ──→ 格式属性报告（适合排版诊断/修复验证）
                         ↑
                    只输出20字定位标记
                    不输出段落全文（省 Token）
```

### 安全沙盒（三层防护 + Docker 隔离）

```
Agent 生成的代码 → Layer 1: AST 静态分析（拦截危险模式）
                → Layer 2: builtins 白名单（运行时纵深防御）
                → Layer 3: 子进程隔离 + 超时强杀（资源兜底）
                → Layer 4: Docker 容器隔离（OS 级沙盒）
```

## 🐳 Docker 部署

### 单容器运行

```bash
docker build -t docmaster-agent .
docker run -d -p 8000:8000 -v ./config:/app/config docmaster-agent
```

### 微服务架构（Docker Compose）

```bash
docker compose up -d    # 一键启动 Agent + Sandbox 两个容器
docker compose down     # 停止并清理
```

```
┌─── Docker 内部网络 ───────────────────┐
│                                       │
│  agent (8000 对外)                    │
│    ├── FastAPI + ReAct Agent          │
│    └── 调用 sandbox 执行代码           │
│           ↕ 内部通信                   │
│  sandbox (8001 仅内部)                │
│    ├── 独立沙盒微服务                  │
│    └── 资源限制: 1 CPU / 256MB        │
│                                       │
└───────────────────────────────────────┘
```

## 📄 License

MIT
