# Kylin OS Agent v2 — LangGraph 重构架构说明

> 本文档回答三个问题：改了什么、为什么这样改、产生了什么影响。

---

## 一、改了什么（文件级对照）

| 旧版文件 (app/) | 新版文件 (app_v2/) | 变化类型 |
|---|---|---|
| `app/agent/orchestrator.py` | `app_v2/graph/nodes.py` + `edges.py` + `builder.py` + `state.py` | **重构**：1 个文件拆成 4 个 |
| `app/model/adapter.py` | `app_v2/model/chat_model.py` | **替换**：urllib → LangChain ChatOpenAI |
| `app/tools/registry.py` | `app_v2/tools/registry.py` | **简化**：ToolSpec 类去掉，只剩 list |
| `app/tools/system_tools.py` | `app_v2/tools/system_tools.py` | **加 @tool**：函数不变，加装饰器 |
| `app/tools/command_runner.py` | `app_v2/model/command_runner.py` | **移动 + 不动**：原样保留 |
| `app/safety/guard.py` | `app_v2/safety/guard.py` | **变薄**：只做检查，不做路由 |
| `app/approval/service.py` | `app_v2/approval/interrupt.py` | **替换**：SQLite 表 → interrupt() |
| `app/memory/store.py` | `app_v2/memory/checkpointer.py` | **替换**：手写 CRUD → LangGraph Checkpointer |
| `app/audit/logger.py` | `app_v2/audit/logger.py` | **简化**：移除依赖回调 |
| `app/main.py` | `app_v2/main.py` | **小改**：orchestrator → runner |
| — | `app_v2/graph/runner.py` | **新增**：图和 FastAPI 翻译层 |
| — | `requirements-v2.txt` | **新增**：依赖声明 |

---

## 二、每一个变化为什么这样改

### 2.1 Orchestrator → StateGraph 四件套

**旧版问题**：`orchestrator.handle()` 是一个 200+ 行的函数，把**流程编排**和**具体操作**混在一起。想加一步（比如"plan 之后做权限检查"），必须在函数体里插入代码，并改动后续所有 if/else。

**新版做法**：

```
state.py   → 数据怎么定义（State 形状）
nodes.py   → 每一步干什么（纯函数，互不调用）
edges.py   → 步与步怎么连（声明式路由规则）
builder.py → 把节点和边 compile() 成一个可调用的图
```

**为什么拆成四个文件**：
  - 旧版是一个"控制中心"（所有逻辑集中）
  - 新版是"每个文件一个职责"（改路由不影响改节点逻辑）
  - 这就是 LangGraph 官方推荐的项目结构

### 2.2 urllib → LangChain ChatModel

**旧版问题**：`adapter.py` 用 urllib 手写 HTTP 请求。换一个模型需要手写新客户端。

**新版做法**：用 `langchain_openai.ChatOpenAI`，把 base_url 指向 DeepSeek。换模型只需换一行初始化代码。

**影响**：你以后面试/工作中看到的 90% agent 项目都会用 LangChain 的 ChatModel，现在你看到的就是这个标准接口。

### 2.3 ToolSpec + ToolRegistry → @tool + list

**旧版问题**：ToolSpec 有 9 个字段（name, description, risk, permission, execution_mode...），手动管理注册和调度。

**新版做法**：函数上加 `@tool` 装饰器，框架自动从函数签名 + docstring 生成 JSON Schema。调度逻辑（能跑/不能跑）交给图的条件边。

**影响**：工具注册从"填一个 9 字段的 dataclass"变成"加一个装饰器"。简洁度差异巨大。

### 2.4 SafetyGuard 变薄

**旧版问题**：Guard 既做检查，又做决策（return deny 给 orchestrator）。

**新版做法**：Guard 只做"检查"——输入文本，输出 `{risk_level, reasons}`。路由决策（deny 时走 deny_node）交给 `edges.py` 的条件边。

**影响**：把"判断危险"和"危险后怎么办"解耦。Guard 复用场景变多（可以随时给别的系统用）。

### 2.5 手写 approval 表 → interrupt()

**旧版问题**：人工审批需要一整条链路：创建 approval 行 → 返回 blocked → 用户调 API → 重新执行。

**新版做法**：节点里 `raise NodeInterrupt(payload)`，agent 暂停，前端展示审批卡片，用户决定后传回，agent 继续。

**影响**：代码量减少（不需要 approval_requests 表、不需要 approve/reject 端点），且是 LangGraph 官方标准做法。

### 2.6 手写 SQLite 记忆 → Checkpointer

**旧版问题**：MemoryStore 手写 conversations/messages 表的 CRUD。

**新版做法**：在 `compile(checkpointer=SqliteSaver(conn))` 时传入，框架自动在每个节点执行完后保存 state。

**影响**：你不再需要写"存消息"的代码。框架还额外支持：从任意历史节点恢复、并发对话隔离（thread_id）。

---

## 三、执行流程对比

### 旧版（手写控制流）
```
HTTP Request
  → orchestrator.handle()
      → [手动] preflight 检查
      → [for] if deny: explain_denial
      → [手动] 调 LLM plan
      → [for] assess_request + assess_plan
      → [for] for step in plan:
      → [手动]   ToolRegistry.call()
      → [手动]   扫描输出
      → [手动] 调 LLM summarize
      → [手动] 写审计日志
  → HTTP Response
```

### 新版（声明式图）
```
HTTP Request
  → runner.run()
      → graph.invoke({"messages": [...]}, config)
          → [框架] 跳到 "preflight" 节点执行
          → [框架] route_after_preflight(state) 决定下一条边
          → [框架] 跳到 "plan" 节点执行
          → [框架] 跳到 "assess_plan" 节点执行
          → [框架] route_after_assess_plan(state)
          → [框架] 跳到 "execute" 节点执行
          → [框架] route_after_execute(state)
          → [框架] 跳到 "summarize" 节点执行
          → [框架] 跳到 END
      → 返回 final_state
  → HTTP Response
```

**核心差异**：旧版的流程控制（for/if）是你写的；新版的流程控制由框架按你声明的边执行。

---

## 四、新的文件结构总览

```
kylin-os-agent/
├── app/                          # 旧版（保留，供对照学习）
├── app_v2/                       # 新版 LangGraph 实现
│   ├── main.py                   #   FastAPI 入口
│   ├── graph/
│   │   ├── state.py              #   ① 状态定义
│   │   ├── nodes.py              #   ② 节点函数（6 个）
│   │   ├── edges.py              #   ③ 条件边（3 个路由函数）
│   │   ├── builder.py            #   ④ 组装 + compile
│   │   └── runner.py             #   ⑤ FastAPI ↔ 图的翻译层
│   ├── tools/
│   │   ├── system_tools.py       #   ⑥ @tool 装饰的 7 个工具
│   │   └── registry.py           #   ⑦ 工具列表
│   ├── model/
│   │   ├── chat_model.py         #   ⑧ LangChain ChatModel
│   │   └── command_runner.py     #   ⑨ 命令执行器（不变）
│   ├── safety/
│   │   └── guard.py              #   ⑩ 安全检查（变薄）
│   ├── approval/
│   │   └── interrupt.py          #   ⑪ 人机交互中断
│   ├── memory/
│   │   └── checkpointer.py       #   ⑫ LangGraph 检查点
│   └── audit/
│       └── logger.py             #   ⑬ 审计日志
├── requirements-v2.txt
└── ARCHITECTURE-v2.md            # ← 你正在读的这个文件
```

---

## 五、还没做的 / TODO

1. **pip install** — 需要你手动装 `requirements-v2.txt`
2. **前端暗色 UI** — 旧版 `app/static/` 的前端需要适配新 API（路径差异）
3. **MCP 协议层** — 旧版 `app/mcp/` 需要迁移到 `app_v2/`
4. **端到端测试** — 需要先装依赖才能验证
5. **真实跑一次** — 目前代码是完整但未运行验证的骨架 + 逻辑

---

## 六、下一步建议

1. 你 pip install 依赖
2. 跑 `python -m uvicorn app_v2.main:app --port 8000`
3. 测试 `/api/chat`
4. 跑通之后 modules 逐一深入讲（每个文件的设计取舍）
