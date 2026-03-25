# Multi-Agent 多角色协作是怎么实现的？

## 为什么要用多 Agent 而不是单 Agent？

### 具体回答

单 Agent 的问题：当任务复杂时（如"全面处理一篇论文"），单 Agent 容易出现：
1. **计划和执行混杂**——LLM 在推理中既要想策略又要处理细节
2. **缺乏验证**——做完就完了，没有"第二双眼睛"检查结果
3. **错误难恢复**——某一步失败后不知道该重试还是跳过

Multi-Agent 的解决方案——**分工**：

| 角色 | 职责 | 可调用的工具 |
|------|------|-------------|
| **Planner** | 分析文档、制定步骤化执行计划 | `analyze_document`, `recall_history` |
| **Executor** | 按计划逐步执行工具 | 所有文档处理工具 |
| **Reviewer** | 读取处理后文档、验证结果 | `read_document` |

**关键设计**：三个角色共享同一个 LLM 实例，通过不同的 System Prompt 切换身份（`build_planner_prompt`, `build_reviewer_prompt`）。这比开 3 个 LLM 连接更高效。

## 流水线的执行流程是怎样的？

### 具体回答

`MultiAgentOrchestrator.run_pipeline()` 的三阶段：

```
Phase 1: Planner（最多 3 步 ReAct 循环）
  ├── 调用 analyze_document 读取文档结构
  ├── 调用 recall_history 查看历史操作
  └── 生成结构化的执行计划（如 "1. format_references 2. create_reference_crossrefs"）

Phase 2: Executor（逐步执行 + 回溯修正）
  ├── 解析计划为 [{index, tool, desc}, ...] 结构化步骤
  ├── 对每一步：执行 → 验证 → 通过则继续 / 失败则回溯
  └── 生成执行报告

Phase 3: Reviewer（最多 3 步 ReAct 循环）
  ├── 调用 read_document 读取处理后的文档
  └── 输出验证报告
```

## 回溯修正（Backtracking）是怎么工作的？

### 具体回答

`_run_executor_with_backtracking()` 实现了**三级回溯策略**，当某一步失败时按顺序升级：

```
执行步骤 → 验证
  ├── ✅ PASS → 继续下一步
  └── ❌ FAIL
        ├── [CRITICAL] 关键性错误 → 直接跳过，标记"需人工处理"
        │                         （如 Word 交叉引用断裂，重试也没用）
        ├── 策略 1: 重试 → 同一步骤最多重试 2 次
        │                  （适合参数错误、临时性问题）
        ├── 策略 2: 重新规划 → 让 Planner 从失败点重新规划剩余步骤
        │                      （只允许 1 次，防止无限重规划循环）
        └── 策略 3: 跳过 → 标记"需人工处理"，继续下一步
```

## 验证系统是怎么判断步骤成功或失败的？

### 具体回答

`_verify_step()` 使用**两层自动化验证**：

**Tier 1: 关键词匹配（零成本）**
- 失败关键词：`失败, 错误, 异常, Error, ❌, Traceback` 等
- 成功关键词：`完成, 成功, 已处理, ✅, 已生成` 等
- 关键性错误关键词：`没有找到引用源, bookmark not defined` 等
- 逻辑：只有失败 → FAIL；只有成功 → PASS；两者都有 → 进入 Tier 2

**Tier 2: LLM mini-review（仅 Tier 1 无法判断时触发）**
- 用一个独立的 LLM 对话，传入步骤描述和执行结果
- 要求 LLM 只回复 "PASS" 或 "FAIL" + 一句话理由
- 如果 LLM 调用失败，保守策略默认 PASS（不因审查失败阻塞流程）

这样做的好处：**大部分情况 Tier 1 就能判断**（不产生额外 LLM 调用费用），只有边界情况才用 Tier 2。

## 重新规划（Re-plan）是怎么做的？

### 具体回答

当重试也失败时，`_re_plan_remaining()` 会给 Planner 传入：
1. 已完成的步骤列表
2. 失败的步骤及原因
3. 要求"不要重复已完成的步骤"

Planner 生成新计划后，用新步骤替换剩余步骤：`steps = steps[:i] + new_steps`

关键限制：**只允许重规划 1 次**（`re_planned` 标志）。因为如果重规划后仍然失败，说明问题本身可能无法自动解决，继续重规划只会浪费 Token。

## Checkpoint（断点续传）是怎么和 Multi-Agent 配合的？

### 具体回答

每完成一个关键节点就自动"存档"：

```python
# Phase 1 完成后存档
state.phase = WorkflowPhase.PLAN_DONE
checkpointer.save(task_id, state)

# Phase 2 每步成功后存档
state.current_step_index = i
checkpointer.save(task_id, state)

# 全部完成后清理存档
checkpointer.clear(task_id)
```

恢复逻辑：`run_pipeline()` 开头先尝试 `checkpointer.load(task_id)`，根据 `state.phase` 决定从哪个阶段恢复。

task_id 由文件路径的 MD5 前 12 位生成，保证同一文件的任务可以正确恢复。
