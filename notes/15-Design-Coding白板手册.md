# Design Coding 白板手册 — 三大核心引擎

> **使用场景**：面试官说"在白板上给我画一下你的系统是怎么跑的"。
> 每个引擎都浓缩成：**一句话定位 → 白板图 → 10 行伪代码 → 亮点钩子 → 追问速答**。
> 不背代码，看懂了就能画出来。

---

## 引擎一：ReAct Agent 循环（agent.py — 719 行 → 10 行）

### 一句话定位

Agent 的"大脑"——接收用户指令，在 Think → Act → Observe 循环中调用工具完成任务，每一步都有错误分类、Token 压缩和 L1 规则兜底。

### 白板流程图

```
用户输入
    │
    ▼
┌──────────────────────┐
│ ① 记忆召回 (RAG)      │  memory.recall_relevant(input)
│ ② 技能匹配 (Skill)    │  skill_manager.match(input)
│ ③ 重建 System Prompt  │  静态根[0] + 动态叶[1]（Prompt Cache 友好）
│ ④ 三明治注入 L1 规则   │  用户消息尾部追加铁律
└──────────┬───────────┘
           │
           ▼
    ┌─── ReAct 循环（最多 max_steps 轮）───┐
    │                                      │
    │  ┌─ Token 水位检查 ──────────────┐   │
    │  │ > 6000: 规则压缩（零成本）      │   │
    │  │ > 8000: LLM 深度摘要           │   │
    │  └───────────────────────────────┘   │
    │          │                            │
    │          ▼                            │
    │   LLM.chat(history, tools)           │
    │          │                            │
    │    ┌─────┴──────┐                    │
    │    │            │                    │
    │ 纯文本        tool_calls             │
    │ (Think完毕)   (要调工具)              │
    │    │            │                    │
    │    ▼            ▼                    │
    │  L1 后校验   执行工具 ──→ Observe    │
    │  保存记忆     ├─ 成功：重置重试计数   │
    │  返回答案     ├─ 临时错误：指数退避重试│
    │              └─ 可修正：结构化引导LLM │
    │                    │                 │
    │                    └──→ 下一轮 ──────┘
    └──────────────────────────────────────┘
```

### 10 行伪代码

```python
def run(user_input):
    recalled = memory.recall_relevant(user_input)     # RAG 召回
    skills = skill_manager.match(user_input)           # 技能匹配
    rebuild_system_prompt(skills, recalled)             # 静态根 + 动态叶
    history.append(user_input + L1_suffix)             # 三明治注入

    for step in range(max_steps):
        if estimate_tokens() > 6000: compress()        # Token 水位压缩
        response = llm.chat(history, tools)            # 调 LLM

        if no tool_calls:                              # Think 完毕
            post_validate_l1()                         # L1 后校验
            save_session(input, response)              # 存记忆
            return response                            # 返回答案

        for tool_call in response.tool_calls:          # Act
            result = execute_tool(tool_call)            # 执行工具
            #   临时错误 → 指数退避自动重试（不消耗步数）
            #   可修正   → 构造结构化 Observation 引导 LLM
            #   致命错误 → 告诉 LLM 放弃这条路
            history.append(result)                     # Observe
```

### 亮点钩子（面试官听到会追问的点）

| 钩子 | 追问 | 一句话回答 |
|------|------|----------|
| "静态根 + 动态叶" | 为什么要分两条 System Message？ | 大模型 API 的 Prompt Cache 匹配最长公共前缀。静态根不变→缓存命中→Token 降价 50%~90% |
| "三明治注入" | 为什么把 L1 规则追加到用户消息尾部？ | 利用 LLM 的近因效应（Recency Bias），紧贴生成起始点的 Token 注意力权重最高 |
| "Token 水位压缩" | 为什么不直接截断？ | 截断会丢失中间的工具执行结果。Tier1 保留工具摘要（零成本），Tier2 让 LLM 做智能压缩（保留关键结论） |
| "错误三级分类" | 怎么让 LLM 自修正？ | 构造结构化 Observation：错误分类 + 修正建议 + 重试计数，引导 LLM 下一步换参数重试，而不是无脑重复 |
| "L1 后校验" | 怎么保证 LLM 一定关闭 Word？ | 不靠 Prompt。代码级硬检查：扫描 _session_tools，用了 Word 工具但没调 close_word → 自动补调 |

---

## 引擎二：三层记忆系统（memory.py — 842 行 → 10 行）

### 一句话定位

对抗大模型的**灾难性遗忘（Catastrophic Forgetting）**——用分级持久化记忆（Hierarchical Memory）让 Agent 越用越聪明，还能自动淘汰"毒记忆"。

### 白板流程图

```
                    ┌─────────────────────────────────────────┐
                    │           L1 宪法记忆 (Constitutional)    │
                    │  carrier: learned_rules.json             │
                    │  特点: 永不过期，硬编码 System Prompt 头部  │
                    │  例: "操作完 Word 必须关闭进程"             │
                    └─────────────────────────────────────────┘

                    ┌─────────────────────────────────────────┐
                    │           L2 经验记忆 (Experiential)      │
 add_reflection()──→│  carrier: memory_vectors.json (reflection)│
                    │  来源: 子任务反思归纳 / L3 晋升            │
                    │  淘汰: FIFO 上限 50 条                    │
                    │  反馈: 工具成功→奖励(+0.1)，失败→惩罚(-0.5)│
                    │  隔离: utility_score < 0 → 删除（毒记忆）  │
                    └──────────────────┬──────────────────────┘
                                       ↑ 晋升: recall_count ≥ 5
                    ┌──────────────────┴──────────────────────┐
 add_conversation()→│       L3 对话记忆 (Conversational)       │
                    │  carrier: memory_vectors.json (conversation)│
                    │  来源: 每轮 Q+A 摘要                      │
                    │  淘汰: 30天 TTL + 召回率末尾淘汰           │
                    └─────────────────────────────────────────┘

                    ┌─────────────────────────────────────────┐
                    │        Working Memory（工作记忆）          │
                    │  carrier: agent.history（纯内存）          │
                    │  压缩: Token 水位线驱动（见引擎一）         │
                    └─────────────────────────────────────────┘

===== 召回流程 =====

用户新问题 → embed(query)
    │
    ▼
遍历所有 L2 + L3 向量，计算三维评分:
    final_score = 0.55 × cos_sim     （语义相关度）
                 + 0.25 × recency    （时间新鲜度 = e^(-days/30)）
                 + 0.20 × utility    （效用分，归一化到 [0,1]）
    │
    ▼
Top-K 过滤 (final_score ≥ 0.60)
    │
    ├─→ 注入到动态叶 Message[1]
    └─→ recall_count += 1（追踪召回频率，为晋升/淘汰提供数据）

===== 层感知冲突消解 =====

存入新记忆时，先查有没有语义重复（cos > 0.92）:
    L3 存入 → 撞 L3: 替换旧的（同层覆盖）
    L3 存入 → 撞 L2: 不动 L2（低层不覆盖高层）
    L2 存入 → 撞 L2: LLM 判断是"覆写"还是"融合"
    L2 存入 → 撞 L3: 删除旧 L3（高层替代低层冗余）
```

### 10 行伪代码

```python
def recall_relevant(query, top_k=3):
    query_vec = embed(query)
    candidates = vector_store.search(query_vec, top_k * 3)  # 粗筛

    for c in candidates:
        semantic  = cosine_sim(query_vec, c.vec)
        recency   = exp(-days_since(c.time) / 30)           # 时间衰减
        utility   = clamp(c.utility_score, 0, 2) / 2        # 效用分
        c.final   = 0.55 * semantic + 0.25 * recency + 0.20 * utility

    results = top_k(sorted(candidates), score >= 0.60)
    for r in results:
        r.recall_count += 1                                  # 召回追踪
        if r.type == "L3" and r.recall_count >= 5:
            promote_to_L2(r)                                 # 晋升
    return format(results)

def on_tool_success(): reward_recalled_L2(+0.1)   # 正反馈
def on_tool_failure(): penalize_recalled_L2(-0.5)  # 负反馈 → <0 则删除
```

### 亮点钩子

| 钩子 | 追问 | 一句话回答 |
|------|------|----------|
| "灾难性遗忘" | 大模型为什么会忘？ | Transformer 注意力随序列长度稀释，早期 Token 被淹没。L1 硬编码 + 三明治注入双保险 |
| "三维评分召回" | 为什么不直接用余弦相似度排序？ | 纯语义会让旧但意义不大的记忆持续占位。加时间衰减和效用分后，"越新越有用"的记忆优先 |
| "毒记忆淘汰" | 什么是毒记忆？ | 被多次召回但每次都伴随工具执行失败的 L2 经验——说明它在误导 Agent。类似推荐系统的负反馈 |
| "层感知冲突" | L3 撞 L2 为什么不覆盖？ | L2 是经过反思提炼的高质量经验，L3 只是原始对话。低层不应覆盖高层，信息置信度不同 |
| "L3 → L2 晋升" | 什么条件晋升？ | recall_count ≥ 5。一条对话记忆被反复需要，说明它有长期价值，值得升级为永久经验 |

---

## 引擎三：安全沙盒（sandbox.py — 573 行 → 10 行）

### 一句话定位

大模型生成的代码可能是"投毒代码"（死循环、文件删除、网络外联）。沙盒提供**三层纵深防御**，确保主引擎永不被击穿。

### 白板流程图

```
LLM 生成的代码
       │
       ▼
┌──── Layer 1: AST 静态分析（编译前拦截）────┐
│                                            │
│  ast.parse(code)                           │
│       │                                    │
│       ▼                                    │
│  SafetyChecker 遍历语法树:                  │
│    visit_Import   → 模块在白名单吗？         │
│    visit_Call     → 调了 exec/eval/__import__？│
│    visit_Attribute → 访问了 os.system/popen？ │
│                                            │
│  有违规 → 直接返回错误（代码根本不会执行）    │
└────────────────────┬───────────────────────┘
                     │ 通过
                     ▼
┌──── Layer 2: Builtins 白名单（运行时限制）──┐
│                                            │
│  构建 safe_builtins 字典:                   │
│    ✅ 保留: int/str/len/range/print...      │
│    ❌ 移除: exec/eval/__import__/compile     │
│    🔒 替换: open → safe_open (只允许 mode=r)│
│    🔒 替换: __import__ → safe_import        │
│             (只放行白名单模块)               │
│                                            │
│  注入: restricted_globals["__builtins__"]   │
│        = safe_builtins                     │
└────────────────────┬───────────────────────┘
                     │
                     ▼
┌──── Layer 3: 进程隔离 + 两级超时强杀 ───────┐
│                                            │
│  multiprocessing.Process(daemon=True)      │
│       │                                    │
│       ▼                                    │
│  proc.join(timeout=5s)                     │
│       │                                    │
│  ┌────┴────┐                               │
│  │         │                               │
│ 正常完成  超时！                             │
│  │         │                               │
│  │    proc.terminate()  ← 第一级：温柔关闭  │
│  │    proc.join(2s)                        │
│  │         │                               │
│  │    还活着？                              │
│  │    proc.kill()       ← 第二级：强杀(SIGKILL)│
│  │    proc.join(1s)                        │
│  │                                         │
│  ▼                                         │
│ 从 Queue 取结果                             │
└────────────────────────────────────────────┘
```

### 10 行伪代码

```python
def execute_sandboxed(code):
    # Layer 1: AST 静态分析
    tree = ast.parse(code)
    errors = SafetyChecker(whitelist, blacklist).visit(tree)
    if errors: return "拦截: " + errors        # 编译前就挡住

    # Layer 2: 构建受限执行环境
    safe_builtins = {保留安全内置函数, 移除exec/eval, 替换open为只读}
    globals = {"__builtins__": safe_builtins}

    # Layer 3: 进程隔离 + 两级强杀
    proc = Process(target=worker, args=(code, globals))
    proc.start()
    proc.join(timeout=5)                       # 最多等 5 秒

    if proc.is_alive():
        proc.terminate()                        # 第一级：温柔关闭
        proc.join(2)
        if proc.is_alive():
            proc.kill()                         # 第二级：SIGKILL
    return result_queue.get()
```

### 亮点钩子

| 钩子 | 追问 | 一句话回答 |
|------|------|----------|
| "AST 静态分析" | 为什么不直接用正则？ | 正则匹配字符串太容易被绕过（变量名混淆、字符串拼接 import）。AST 是语法树级别的检查，不看字符看结构 |
| "两级强杀" | 为什么要 terminate + kill？ | terminate 发 SIGTERM，给进程清理机会。但如果代码 catch 了 SIGTERM 或在系统调用中阻塞，terminate 无效，必须用 kill (SIGKILL) 强杀 |
| "builtins 白名单" | 为什么 Layer 1 过了还需要 Layer 2？ | AST 检查的是源码文本，但 Python 有太多动态特性（`getattr(builtins, 'ev'+'al')`）可以绕过静态分析。运行时白名单是第二道防线 |
| "daemon=True" | 为什么子进程设为 daemon？ | 主进程退出时，daemon 子进程自动被杀。防止沙盒子进程变成孤儿进程吃光系统资源 |
| "Docker 回退" | 为什么有 Docker 还要本地沙盒？ | Docker 沙盒提供 cgroups 级资源限制（256MB 内存上限），但部署时 Docker 不一定可用。代码设计了降级：Docker 优先 → 本地 multiprocessing 兜底 |

---

## 白板速画口诀

> 面试时不要试图画全，画**主干 + 标注亮点**即可。

### 30 秒速画版

```
Agent 引擎:   用户 → [召回+技能] → ReAct循环{LLM→工具→观察} → L1校验 → 回答
              标注: "Token水位压缩" "错误三级分类" "三明治注入"

记忆引擎:     L1(铁律) → L2(经验,向量) ⇄ L3(对话,向量)
              标注: "三维评分召回" "毒记忆淘汰" "L3→L2晋升"

沙盒引擎:     代码 → AST拦截 → builtins白名单 → 进程隔离+强杀
              标注: "两级terminate/kill" "Docker降级"
```

### 面试节奏建议

1. **先画主干骨架**（30 秒）——让面试官看到全貌
2. **在关键节点写上"钩子词"**（如"Token 水位""毒记忆"）——引导追问
3. **面试官追问哪个钩子，就展开那一块**——用上面的伪代码和一句话回答
4. **不要试图把整个流程图都画完**——白板空间有限，画太多反而乱

---

## 三引擎联动全景图（加分项）

> 如果面试官让你"画整个系统的架构"，画这张：

```
┌─────────────────────────────────────────────────────────┐
│                    用户输入                               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
              ┌─────── Agent 引擎 ───────┐
              │                          │
              │  ①召回记忆  ②匹配技能     │
              │         │                │
              │    ReAct 循环             │
              │    ┌────┴────┐           │
              │    │  Think  │←──────┐   │
              │    │  (LLM)  │       │   │
              │    └────┬────┘       │   │
              │         │            │   │
              │    ┌────▼────┐  ┌────┴──┐│
              │    │   Act   │→ │Observe││
              │    │ (工具)  │  │(结果) ││
              │    └────┬────┘  └───────┘│
              │         │                │
              │    沙盒引擎               │
              │    (如果是代码执行工具)     │
              │    AST→Builtins→进程隔离   │
              │                          │
              └────────────┬─────────────┘
                           │
                    ┌──────▼──────┐
                    │  记忆引擎    │
                    │ L1: 铁律    │ ←── 后校验自动补正
                    │ L2: 经验    │ ←── 工具成败反馈
                    │ L3: 对话    │ ←── 本轮Q+A摘要
                    └─────────────┘
```
