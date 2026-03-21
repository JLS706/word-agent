# 🤖 DocMaster Agent

**学术论文排版 AI 智能助手** —— 通过自然语言指令驱动 Word 文档自动化处理。

基于 **ReAct (Reasoning + Acting)** 架构，参考 [OpenManus](https://github.com/FoundationAgents/OpenManus) 和 [smolagents](https://github.com/huggingface/smolagents) 设计理念打造的轻量级 AI Agent 项目。

## ✨ 功能

| 工具 | 功能描述 |
|------|---------|
| `format_references` | 参考文献格式修复（字体统一、Sentence Case、期刊名斜体） |
| `create_reference_crossrefs` | 文献交叉引用生成（[1][2] → 可跳转域代码） |
| `create_figure_crossrefs` | 图注交叉引用生成（图X.Y → 可跳转域代码） |
| `convert_handwritten_captions` | 手写图注转 Word 题注（支持自动编号） |
| `check_acronym_definitions` | 缩写定义检测（检测 MIMO 等缩写是否有全称） |
| `convert_latex_to_mathtype` | LaTeX 公式 → MathType 批量转换 |

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
│   ├── schema.py               # 数据模型
│   ├── llm.py                  # LLM 接口封装
│   ├── agent.py                # ReAct Agent 核心循环
│   └── prompt.py               # Prompt 模板
├── tools/
│   ├── base.py                 # Tool 基类 + 注册表
│   ├── ref_formatter.py        # 阶段A
│   ├── ref_crossref.py         # 阶段B
│   ├── fig_crossref.py         # 阶段C
│   ├── fig_caption.py          # 阶段D
│   ├── acronym_checker.py      # 阶段E
│   └── latex_converter.py      # LaTeX转换
├── Word文献自动化精灵.py        # 原始脚本（被Tool引用）
├── latex.py                    # 原始脚本（被Tool引用）
└── main.py                     # 入口
```

## 🧠 工作原理

```
用户: "帮我处理参考文献格式和交叉引用"
  ↓
Agent (LLM推理): 需要先调用 format_references，再调用 create_reference_crossrefs
  ↓
执行 Tool 1: format_references → 格式修复完成
  ↓
执行 Tool 2: create_reference_crossrefs → 交叉引用生成完成
  ↓
Agent: "已完成！格式修复处理了25条文献，交叉引用替换了42处引用。"
```

## 📄 License

MIT
