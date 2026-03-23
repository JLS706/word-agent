---
name: 数据分析与代码执行
description: 使用 Python 沙盒进行数据统计、文本分析、词频计算等编程任务
trigger_keywords: [统计, 分析, 计算, 字数, 词频, 代码, 编程, 脚本, python, 数据, 比较, 对比, 相似度]
tools: [execute_python]
priority: 5
---

## 使用原则

1. **预设工具优先**：能用预设工具完成的，不要写代码
2. **主动造工具**：预设工具做不到时，用 `execute_python` 自己写代码解决
3. **沙盒是安全的**：代码在隔离环境中执行，不会影响系统

## 适用场景

- 用户问"统计参考文献的年份分布" → 写正则+统计代码
- 用户问"检查不同章节的用词风格" → 写词频分析代码
- 用户问"算一下论文各章节的字数比例" → 写字符串分析代码
- 用户问"对比两段文字的相似度" → 用 difflib 写比较脚本

## 可用模块

`re`, `math`, `statistics`, `collections`, `json`, `csv`, `datetime`, `difflib`, `itertools`, `functools`, `string`, `random`

## 注意

- 沙盒是只读的：可以读文件、做计算，但不能写文件或修改文档
- 每次执行超时限制 5 秒
- 不能 import os, sys, subprocess 等系统模块
