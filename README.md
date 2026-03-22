# 🤖 DocMaster Agent

**学术论文排版 AI 智能助手** —— 通过自然语言指令驱动 Word 文档自动化处理。

基于 **ReAct (Reasoning + Acting)** 架构，参考 [OpenManus](https://github.com/FoundationAgents/OpenManus) 和 [smolagents](https://github.com/huggingface/smolagents) 设计理念打造的轻量级 AI Agent 项目。

## ✨ 功能

### 文档处理工具

| 工具 | 功能描述 |
|------|---------| 
| `format_references` | 参考文献格式修复（字体统一、Sentence Case、期刊名斜体） |
| `create_reference_crossrefs` | 文献交叉引用生成（[1][2] → 可跳转域代码） |
| `create_figure_crossrefs` | 图注交叉引用生成（图X.Y → 可跳转域代码） |
| `convert_handwritten_captions` | 手写图注转 Word 题注（支持自动编号） |
| `check_acronym_definitions` | 缩写定义检测（检测 MIMO 等缩写是否有全称） |
| `convert_latex_to_mathtype` | LaTeX 公式 → MathType 批量转换 |

### 高级能力

| 能力 | 描述 |
|------|---------| 
| `execute_python` | 安全沙盒代码解释器（三层安全防护 + 进程级隔离） |
| `save/forget/list_learned_rules` | Agent 自学习系统（将经验存为规则，持续改进） |
| Multi-Agent 流水线 | Planner → Executor → Reviewer 三角色协作 |
| 结构化错误恢复 | 三级错误分类 + 自动重试 + 引导 LLM 自修正 |
| 回溯修正 (Backtracking) | 逐步执行→每步验证→失败时自动回溯 |

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 LLM API

```bash
cp config/config.example.toml config/config.toml
```

编辑 `config/config.toml`，填入你的 API Key。推荐使用免费方案：

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
- `帮我格式化参考文献`
- `生成文献交叉引用`
- `检查一下缩写有没有定义`
- `把图注转成Word题注，然后生成图注交叉引用`

## 🏗️ 项目架构

```
agent/
├── config/config.toml          # LLM 配置
├── core/
│   ├── schema.py               # 数据模型（Message, ToolCall, AgentState）
│   ├── llm.py                  # LLM 接口封装（OpenAI 兼容）
│   ├── agent.py                # ReAct Agent 核心（含结构化错误恢复）
│   ├── prompt.py               # System Prompt 模板
│   ├── memory.py               # 本地持久化记忆系统
│   └── multi_agent.py          # Multi-Agent 流水线（含回溯修正）
├── tools/
│   ├── base.py                 # Tool 基类 + ToolRegistry
│   ├── ref_formatter.py        # 参考文献格式化
│   ├── ref_crossref.py         # 文献交叉引用
│   ├── fig_crossref.py         # 图注交叉引用
│   ├── fig_caption.py          # 手写图注转题注
│   ├── acronym_checker.py      # 缩写检测
│   ├── latex_converter.py      # LaTeX 转换
│   ├── code_interpreter.py     # 安全沙盒代码解释器
│   ├── learned_rules.py        # Agent 自学习规则
│   └── pipeline.py             # 文档分析（Planner 用）
├── memory/                     # 持久化存储
│   ├── history.json            # 对话历史
│   └── learned_rules.json      # 学习到的规则
└── main.py                     # 入口
```

## 🧠 工作原理

### 单 Agent 模式（ReAct 循环）

```
用户: "帮我处理参考文献格式和交叉引用"
  ↓
Agent (Think): 需要先调用 format_references，再调用 create_reference_crossrefs
  ↓
Agent (Act): 执行 format_references → 结果/错误
  ↓
Agent (Observe): 成功 → 继续 | 失败 → 结构化错误引导自修正
  ↓
Agent: "已完成！格式修复25条，交叉引用替换42处。"
```

### Multi-Agent 流水线（含回溯修正）

```
Phase 1 — Planner:  分析文档 → 制定执行计划 [Step1, Step2, ..., StepN]
  ↓
Phase 2 — Executor (逐步执行 + 回溯):
  Step 1 → 验证 ✅ → 继续
  Step 2 → 验证 ✅ → 继续
  Step 3 → 验证 ❌ → 关键错误? 🚨 汇报人类
                    → 普通错误? 🔄 重试 → 🧭 重规划 → ⏩ 跳过
  ↓
Phase 3 — Reviewer: 读取处理后文档 → 验证并输出报告
```

### 安全沙盒（三层防护）

```
Agent 生成的代码 → Layer 1: AST 静态分析（拦截危险模式）
                → Layer 2: builtins 白名单（运行时纵深防御）
                → Layer 3: 子进程隔离 + 超时强杀（资源兜底）
```

## 📄 License

MIT

