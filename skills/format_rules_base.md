---
name: 中文学术论文通用排版规范
description: 通用的中文学术论文格式规范兜底，适用于大多数高校毕业论文和中文期刊
trigger_keywords: [论文, 排版, 格式, 检查格式, 字体, 缩进, 行距, 对齐, word, 文档, 格式检查]
tools: [inspect_document_format, analyze_document]
priority: 5
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

## 通用格式规范说明

本 Skill 提供中文学术论文最常见的格式规范作为兜底默认值：

- **正文**：宋体（中文）+ Times New Roman（英文/数字），小四号（12pt），首行缩进2字符（0.74cm），1.5倍行距，两端对齐
- **一级标题**：黑体，三号（15-18pt），加粗，居中
- **二级标题**：黑体，四号（13-15pt），加粗

如果你的学校或期刊有特殊要求，可以创建一个专用 Skill 文件，只覆盖有差异的字段，本 Skill 中未被覆盖的字段会自动作为默认值填充。
