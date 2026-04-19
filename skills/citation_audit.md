---
name: 引用溯源审计
description: 对综述论文中的引用进行忠实度审计——检查作者的引用是否准确反映了原文内容
trigger_keywords: [引用, 溯源, 审计, 验证, 忠实, 曲解, 过度引申, citation, verify, 原文对比, 引用检查, 交叉验证]
tools: [verify_citations, index_document, search_document, read_document]
priority: 8
---

## 引用溯源审计（Citation Verification）

### 使用场景

用户需要验证综述/论文中引用的准确性：
- "帮我检查论文里的引用是否准确"
- "验证第3章的引用有没有过度引申"
- "对比一下我写的和原文是否一致"

### 前置条件

用户需要提供：
1. **综述正文**：待审计的 Word 文档
2. **原文文献**：被引用的论文（Word/PDF），并标注对应的引用编号

### 执行流程

1. 先用 `read_document` 了解综述结构
2. 收集用户提供的原文文献路径和对应编号
3. 调用 `verify_citations` 工具执行审计：
   - 自动提取综述中所有带 [N] 标记的主张句
   - 自动索引每篇原文文献（带缓存，不重复）
   - 在原文中检索最相关的段落
   - LLM 以严苛审稿人视角判定忠实度
4. 输出审计报告，标注 FAITHFUL / MINOR_ISSUE / MAJOR_ISSUE / UNSUPPORTED

### 输出格式

报告包含：
- 总体统计（忠实率、各类问题数量）
- 问题清单（逐条列出有偏差的引用，附原文证据）
- 每条问题的详细分析和改进建议

### 注意事项

- 审计质量取决于原文文献的完整性——如果提供的不是完整论文，可能会误判为 UNSUPPORTED
- 建议先用 `index_document` 单独索引一篇文献，确认索引质量后再批量审计
- `max_claims` 参数可控制审计范围，避免 API 调用过多
