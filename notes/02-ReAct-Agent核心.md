# ReAct Agent 的核心实现是怎样的？

## Agent 的核心循环是怎么工作的？

### 具体回答

`core/agent.py` 中的 `Agent.run()` 方法实现了标准的 ReAct 循环：

```
用户输入 → [记忆召回 + 技能匹配 → 重建 System Prompt]
         → for step in range(max_steps):
             LLM.chat(history, tools)
               ├── 返回 tool_calls → 执行工具 → 结果加入 history → 继续循环
               └── 返回纯文本    → 任务完成 → 保存记忆 → 返回答案
```

关键设计决策：
1. **每一步都把完整 history 发给 LLM**——LLM 看到所有历史推理和工具结果，保证上下文连贯
2. **max_steps 防止死循环**——默认 10 步上限，超过强制停止
3. **System Prompt 动态构建**——每次 run() 开始时根据用户输入重新构建，注入记忆和技能

## System Prompt 是怎么构建的？包含哪些信息？

### 具体回答

`Agent._build_system_prompt()` 会把以下内容拼入 System Prompt：

1. **工具描述** (`tool_desc`)：所有已注册工具的名称和功能说明
2. **记忆上下文** (`memory_context`)：最近 3 次操作记录 + RAG 召回的相关历史
3. **技能上下文** (`skills_context`)：根据用户输入匹配到的 Skill 手册内容

这意味着 System Prompt 不是固定的——用户说"格式化参考文献"和"检查缩写"时，注入的技能和召回的历史完全不同。**按需加载，省 Token**。

## LLM 是怎么调用工具的？用的什么标准？

### 具体回答

使用 **OpenAI Function Calling** 标准。所有工具通过 `ToolRegistry.to_openai_tools()` 转为 JSON Schema 格式：

```json
{
  "type": "function",
  "function": {
    "name": "format_references",
    "description": "格式化参考文献...",
    "parameters": { "type": "object", "properties": { ... } }
  }
}
```

LLM 的响应中如果包含 `tool_calls` 字段，Agent 就解析出工具名和参数，调用对应工具的 `execute()` 方法，然后把结果以 `role: tool` 的消息追加到 history。

## 为什么不用 JSON 模式让 LLM 自己输出动作？

### 具体回答

Function Calling 比自由格式 JSON 更可靠：
1. **LLM 端约束**——模型在生成时就受到 tools schema 约束，参数类型和必填字段由模型自动遵守
2. **不需要手动解析**——OpenAI SDK 直接返回结构化的 `tool_calls` 对象
3. **兼容性好**——Gemini、智谱、硅基流动等都兼容 OpenAI 的 Function Calling 格式

## _session_tools 和 _session_file 是做什么的？

### 具体回答

这是**会话级别的元数据追踪**：
- `_session_tools: list[str]`：记录本轮对话中执行过的工具名列表
- `_session_file: str`：记录本轮操作的文件路径（取第一个包含 file_path 参数的工具调用）

当 Agent 完成任务后，`_save_session()` 会把这些信息写入 Memory，实现**跨会话记忆**。比如下次用户说"上次那个文件再处理一下"，Agent 能从记忆中召回上次的文件路径。

## run_async() 异步流式状态机是什么？

### 具体回答

`Agent.run_async()` 是 Agent 的核心异步入口，将 ReAct 循环切片为一系列 `StreamEvent`，通过 `async generator` 向外 yield。上层消费者（终端 / WebSocket / DelegateTaskTool）只需 switch-case 事件类型即可渲染：

| 事件类型 | 含义 | 触发时机 |
|---------|------|---------|
| `text` | LLM 文本增量（打字机效果） | 流式推理中每个 chunk |
| `tool_start` | 开始执行工具 | 进入工具调用前 |
| `tool_progress` | 工具内部进度心跳 | 工具调用 `report_progress()` |
| `tool_end` | 工具执行完成 | 工具返回结果后 |
| `tool_timeout` | 工具心跳停滞触发熔断 | 看门狗超时击杀 |
| `error` | 错误事件 | LLM 调用失败 / L1 违规 |
| `finish` | 任务完成 | 最终回答生成后 |

**关键设计**：
1. **LLM 流式响应**：`async for chunk in stream` 逐 chunk yield `text` 事件
2. **工具执行**：`asyncio.to_thread` + 线程安全 `Queue` 事件泵，工具在工作线程执行，进度通过 Queue 冒泡到主事件循环
3. **随时可中断**：调用方 `break` 即可终止生成器，`finally` 块确保 Word 兜底关闭

旧的同步 `run()` 已标记 `@deprecated`，仅保留向后兼容。

## Token 水位线压缩是怎么防上下文爆炸的？

### 具体回答

`_compress_history()` 实现了**两级 Token 水位线**驱动的上下文压缩：

```
Token 水位 < 6000  → 不压缩
6000 ≤ Token < 8000 → Tier 1: 纯规则截取（零 LLM 成本）
Token ≥ 8000        → Tier 2: 调 LLM 做智能摘要

压缩前: [System, User, LLM₁, Tool₁, ..., LLM₇, Tool₇]
压缩后: [System, User, Scratchpad, LLM₆, Tool₆, LLM₇, Tool₇]
         ↑保留头部2条    ↑中间压缩为1条    ↑保留尾部4条
```

**Tier 1**：提取中间每条 Tool 消息的第一行（80字），拼接为 `[已完成步骤] tool_a: xxx; tool_b: yyy`
**Tier 2**：把中间内容截断到 2000 字符，让 LLM 压缩为 3 句话摘要

Token 估算用 `len(text) // 2` 近似（中英文混合误差 ±20%），不引入 tiktoken 依赖。

## L1 三明治注入和后校验是怎么工作的？

### 具体回答

L1（宪法记忆）的防护分为**三层注入 + 一层后校验**，形成首尾夹击：

**注入层**：
1. **System Prompt 头部**：L1 规则写入静态根 Message[0] 的 `{learned_rules_context}` 区域
2. **System Prompt 尾部**：L1 规则在末尾重复一遍（`{learned_rules_reminder}`），对抗 LLM 注意力衰减
3. **User 消息后缀**（三明治注入）：`build_l1_user_suffix()` 在用户输入末尾追加 L1 规则，紧贴 LLM 生成起始点，利用**近因效应（Recency Bias）**最大化规则服从度

**后校验层**（代码级硬保障）：
- `_post_validate_l1()`：Agent 生成最终回答后，直接检查 `_session_tools` 里的实际行为
- 不依赖 LLM 是否"注意到"了规则，而是代码级别的事实核查
- 检测到违规时自动执行修正动作（如兜底关闭 Word 进程）

## 心跳看门狗事件泵是怎么工作的？

### 具体回答

`run_async()` 中的工具执行采用**信号驱动事件泵**（非 busy-polling），核心是线程安全 Queue + asyncio.Event：

```
工具线程                          主事件循环（事件泵）
  │                                  │
  ├── report_progress(50, "处理中")   │
  │   └── queue.put((50, msg, {}))   │
  │   └── loop.call_soon_threadsafe  │
  │       (wake_event.set)──────────→ wake_event 触发
  │                                  ├── queue.get_nowait() → yield tool_progress
  │                                  ├── last_heartbeat = now()  ← 心跳续命
  │                                  └── wake_event.clear()
  │                                  │
  │ （长时间无心跳）                    │
  │                                  ├── stall_sec > STALL_TIMEOUT ?
  │                                  │   └── COMSafeLock.kill_pids() → 精准击杀僵尸 Word
  │                                  │   └── await wait_for(task, 3s) → 等线程跑完 finally
  │                                  │   └── yield tool_timeout → 结构化错误回注历史
```

**动态阈值租约**：工具可以通过 `report_progress(metadata={"temp_timeout": 30})` 临时申请更长的超时。大文档 `Fields.Update()` 黑盒执行时申请 30 秒租约，退出时 `finally` 块强制弹回 5 秒基础阈值。实现"平时零容忍，黑盒弹性放行"。

## Coordinator 人设是怎么自动探测的？

### 具体回答

`_build_system_prompt()` 中一行代码实现自动切换：

```python
is_coordinator = self.tools.get("delegate_task") is not None
```

- 主 Agent 的 registry 注册了 `delegate_task` → 自动使用 `COORDINATOR_PROMPT_STATIC`（蜂群指挥官人设）
- Worker 的 registry 经 `exclude({"delegate_task"})` 过滤 → 自动使用 `SYSTEM_PROMPT_STATIC`（Executor 人设）

无需手动配置 `role` 参数，纯粹由工具注册表的组成决定身份。
