---
name: 论文排版标准流
description: 学术论文排版全流程，包括参考文献格式化、交叉引用、图注处理、缩写检测等
trigger_keywords: [论文, 排版, 参考文献, 图注, 交叉引用, 缩写, 文献, 格式, 图片, caption, 引用, word, 文档处理]
tools: [format_references, create_reference_crossrefs, create_figure_crossrefs, convert_handwritten_captions, check_acronym_definitions, convert_latex_to_mathtype]
priority: 10
---

## 执行顺序（严格遵守）

D(手写图注转题注) → C(图注交叉引用) → A(参考文献格式化) → B(文献交叉引用) → E(缩写检测) → LaTeX转换

## 各步骤说明

1. **convert_handwritten_captions (D)** — 必须最先执行。将手写的"图X.Y ..."格式转为 Word 原生题注，后续的图注交叉引用依赖此步骤的结果。
2. **create_figure_crossrefs (C)** — 依赖步骤1。扫描文档中的"图X.Y"引用，创建指向题注的交叉引用域代码。
3. **format_references (A)** — 参考文献格式修正，包括字体统一、Sentence Case、期刊名斜体处理。
4. **create_reference_crossrefs (B)** — 依赖步骤3。将正文中的 [1][2] 等引用转为指向参考文献列表的交叉引用域代码。
5. **check_acronym_definitions (E)** — 检测 MIMO、OFDM 等缩写是否在首次出现时给出了完整定义。
6. **convert_latex_to_mathtype** — 可选。将文档中的 LaTeX 公式批量转为 MathType 对象。

## 注意事项

- 默认使用另存副本模式，不覆盖原文件
- 如果 analyze_document 显示某步骤不需要，可以跳过
- 每步执行后检查返回结果中的处理条目数
