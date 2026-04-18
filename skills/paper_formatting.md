---
name: 论文排版标准流
description: 学术论文排版全流程，包括格式诊断、参考文献格式化、交叉引用、图注处理、缩写检测等
trigger_keywords: [论文, 排版, 参考文献, 图注, 交叉引用, 缩写, 文献, 格式, 图片, caption, 引用, word, 文档处理, 检查格式, 字体, 缩进, 行距]
tools: [inspect_document_format, format_references, create_reference_crossrefs, create_figure_crossrefs, convert_handwritten_captions, check_acronym_definitions, convert_latex_to_mathtype]
priority: 10
config:
  format_rules:
    正文:
      font_cn: "宋体"
      font_en: "Times New Roman"
      font_size: 12.0
      first_indent_cm: 0.74
      line_spacing: 1.5
      alignment: 3
    标题 1:
      font_cn: "黑体"
      font_size_min: 15.0
      font_size_max: 18.0
      bold: true
      alignment: 1
    标题 2:
      font_cn: "黑体"
      font_size_min: 13.0
      font_size_max: 15.0
      bold: true
  ref_format_config:
    font_cn: "宋体"
    font_en: "Times New Roman"
    font_size: 10.5
  acronym_whitelist:
    - IEEE
    - ACM
    - DOI
    - HTTP
    - URL
    - PDF
    - USB
    - GPS
---

## 执行顺序（严格遵守）

0(格式诊断) → D(手写图注转题注) → C(图注交叉引用) → A(参考文献格式化) → B(文献交叉引用) → E(缩写检测) → LaTeX转换 → F(格式验证)

## 各步骤说明

0. **inspect_document_format (格式诊断)** — 建议在排版前先执行。分页检查文档的样式、字体、缩进、行距等格式属性，生成诊断报告。该工具只查格式不输出内容（省 Token），每段仅显示前 20 字作为定位标记。
1. **convert_handwritten_captions (D)** — 必须最先执行。将手写的"图X.Y ..."格式转为 Word 原生题注，后续的图注交叉引用依赖此步骤的结果。
2. **create_figure_crossrefs (C)** — 依赖步骤1。扫描文档中的"图X.Y"引用，创建指向题注的交叉引用域代码。
3. **format_references (A)** — 参考文献格式修正，包括字体统一、Sentence Case、期刊名斜体处理。
4. **create_reference_crossrefs (B)** — 依赖步骤3。将正文中的 [1][2] 等引用转为指向参考文献列表的交叉引用域代码。
5. **check_acronym_definitions (E)** — 检测 MIMO、OFDM 等缩写是否在首次出现时给出了完整定义。
6. **convert_latex_to_mathtype** — 可选。将文档中的 LaTeX 公式批量转为 MathType 对象。
7. **inspect_document_format (格式验证)** — 排版完成后再次执行，Diff 对比修复前后的变化，确认格式问题已修正。

## Tool-Skill 分离说明

本 Skill 的 `config` 块为工具提供**领域知识参数**：
- `format_rules` → 注入到 `inspect_document_format` 工具，定义格式检查标准
- `ref_format_config` → 注入到 `format_references` 工具，定义参考文献字体字号
- `acronym_whitelist` → 注入到 `analyze_document` 工具，定义缩写白名单

**没有加载本 Skill，上述工具将无法执行**（工具不含硬编码默认值）。

## 工具职责分离

- `inspect_document_format` — **查格式**（样式/字体/缩进/行距），不输出内容
- `read_document` — **查内容**（全文/参考文献/结构），不查格式
- 两个工具互补使用，不要混淆职责

## 注意事项

- 默认使用另存副本模式，不覆盖原文件
- 如果 analyze_document 显示某步骤不需要，可以跳过
- 每步执行后检查返回结果中的处理条目数
- inspect_document_format 每次查看 20 段，用 start_para 参数分页
