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
