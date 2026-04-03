---
name: 示例：仿宋正文排版规范
description: 演示如何创建特化 Skill——仅覆盖与通用规范不同的字段
trigger_keywords: [仿宋, 示例规范]
tools: [inspect_document_format]
priority: 10
config:
  format_rules:
    正文:
      font_cn: "仿宋_GB2312"
      font_size: 16.0
      line_spacing: 1.25
    标题 1:
      font_cn: "方正小标宋简体"
      font_size_min: 22.0
      font_size_max: 22.0
    标题 2:
      font_cn: "楷体_GB2312"
      font_size_min: 16.0
      font_size_max: 16.0
      bold: false
---

## 示例：如何创建特化 Skill

这是一个演示用的特化 Skill，展示了如何只覆盖与通用规范不同的字段。

### 本 Skill 覆盖了什么？

| 字段 | 通用规范 | 本 Skill |
|------|---------|---------|
| 正文中文字体 | 宋体 | **仿宋_GB2312** |
| 正文字号 | 12pt (小四) | **16pt (三号)** |
| 正文行距 | 1.5倍 | **1.25倍** |
| 标题1字体 | 黑体 | **方正小标宋简体** |
| 标题2字体 | 黑体 | **楷体_GB2312** |
| 标题2加粗 | true | **false** |

### 本 Skill 没有覆盖的字段（自动使用通用规范兜底）

- 正文西文字体 → Times New Roman
- 正文首行缩进 → 0.74cm
- 正文对齐方式 → 两端对齐
- 标题1对齐方式 → 居中

### 如何创建你自己的特化 Skill

1. 复制本文件，改名为你学校/期刊的名字（如 `format_rules_xxx大学.md`）
2. 修改 `trigger_keywords` 为你学校/期刊的关键词
3. 修改 `config.format_rules` 中需要特化的字段
4. 不需要的字段直接删除（会自动使用通用规范兜底）
5. 如果某个字段不想检查，设为 `null`（如 `line_spacing: null`）
