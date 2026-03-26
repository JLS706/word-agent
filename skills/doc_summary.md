---
name: 文档摘要
keywords: [总结, 概括, 摘要, 概要, 大意, 梗概, 全文, 综述, 内容简介]
---

# 文档全文摘要

## 何时使用
当用户要求**理解整篇文档**时使用，典型问法：
- "帮我总结一下这篇论文"
- "这篇文档讲了什么"
- "给个概要"

## 何时不使用
当用户要**查找特定内容**时，应使用 `search_document`（RAG 检索），例如：
- "论文里提到 MIMO 的地方在哪"
- "第三章的方法是什么"

## 决策流程
```
用户需求 → 需要整体理解？
  ├─ 是 → summarize_document（Map-Reduce 全文压缩）
  └─ 否 → 需要找特定内容？
       ├─ 是 → index_document + search_document（RAG 检索）
       └─ 否 → read_document（直接读取）
```

## 工具用法
```
summarize_document(file_path="论文.docx", detail_level="brief")
  - brief: 3-5 句话概要
  - detailed: 300-500 字详细概要
```

## 注意事项
- 此工具会自动分段调用 LLM，适合长文；短文（<2000字）直接返回全文
- 首次使用较慢（需要逐段生成摘要），但结果覆盖全文
