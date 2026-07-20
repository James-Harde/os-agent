# app_v4 实现日志

## 迁移策略

基于 app_v2 骨架迁移到 app_v4，保留架构风格，补齐缺失能力。

## app_v2 当前状态摘要

### 入口
- `app_v2/main.py` — FastAPI，2 个端点：`/api/health`、`/api/chat`
- 限流：slowapi 10/minute

### API 结构
- POST `/api/chat` 接收 `{message, conversation_id?}`，返回 `{conversation_id, intent, guard_decision, guard_reasons, tool_calls, answer, answer_source}`

### Graph 结构（6 节点 + 条件边）
```
START → preflight → [deny | plan → assess_plan → [deny | execute → [summarize | deny]]] → END
```
- `preflight_node`：SafetyGuard.check_input()
- `plan_node`：LLM 生成 {intent, plan}
- `assess_plan_node`：SafetyGuard.check_plan()
- `execute_node`：循环调用 @tool.invoke()
- `summarize_node`：LLM 生成最终回答
- `deny_node`：生成拒绝回答

### State
- `AgentState(TypedDict)`：messages/intent/plan/guard_decision/guard_reasons/tool_calls/answer/answer_source
- 使用 add_messages reducer 累加 messages 和 tool_calls

### Tool
- 7 个 @tool 函数：disk_usage, directory_usage, port_lookup, process_list, system_logs, service_status, prompt_injection_scan
- CommandRunner 白名单执行系统命令

### Memory
- SqliteSaver checkpointer，存 data/agent_v2.db
- thread_id = conversation_id

### Audit
- AuditLogger SQLite，记录每次调用结果
- 表：audit_logs

### Approval
- interrupt() + NodeInterrupt，LangGraph 标准 HITL

## 已知缺陷（来自 AGENT-CHAIN.md）
- B06：匿名请求共用 thread_id="default" → app_v4 改为自动生成 UUID
- B04：tool_calls 用 add_messages → app_v4 改为普通 list
- B09：工具输出扫描结果没影响后续行为 → app_v4 接入路由决策
- B08：审批和审计未进主链路 → app_v4 接入
- B07：空计划被错判为高风险 → app_v4 修正路由

## app_v4 迁移要点
1. 保留 6 节点图结构，修正已知缺陷
2. 新增 Trace 系统（带 run_id）、traces 查询端点
3. 新增 /api/traces/{run_id} 端点
4. 结构化工具结果（duration_ms、status、source）
5. 审计接入主链路
6. 补齐自动化测试

---

## P3 — README Roadmap 剩余 4 项能力（本轮完成）

### 1. 短期/长期记忆分层 + 渐进披露
- 新增 `app_v4/memory/long_term.py`：SQLite 表 `long_term_memories`，存跨 thread 结论（kind=conclusion）和用户画像（kind=profile，按 key 取最新值）。
- `plan_node`：工具 > 5 时按描述关键词重叠度排序，只暴露 Top-5 给 LLM（渐进披露），hidden_tools 记入 trace。
- 记忆召回注入 system prompt，让历史结论影响当次规划。
- `runner.run_agent` 结束后统一写入结论 + 画像累积。
- 新增 `app_v4/graph/state.py` 字段：memory_context / seen_plans / loop_detected。

### 2. RAG 最小链（零 heavy 依赖）
- 新增 `app_v4/rag/` 包：chunk（字符滑动窗口+重叠）/ vectorizer（稀疏 TF-IDF + 余弦）/ bm25（BM25Okapi 简化版）/ pipeline（向量召回→BM25 两阶段）/ eval（Recall@k）。
- 中文采用「单字」token，无需 jieba。
- 内置 8 条样本评测集，实测 **Recall@1 = 1.0，Recall@3 = 1.0**。
- 不引入 faiss/chroma/sentence-transformers，纯 Python 标准库实现。

### 3. SSE 流式响应
- 新增 `POST /api/chat/stream`：FastAPI `StreamingResponse`，media_type=text/event-stream。
- 渐进返回：preflight → plan（含渐进披露标志）→ execute（tool_calls）→ summarize（answer）→ done。
- `runner.streaming_agent` 基于 `graph.astream(stream_mode="updates")`；边流边合并状态，done 事件直接从合并态取 answer（避免二次读 DB）。
- 异步路径使用 `AsyncSqliteSaver`（aiosqlite），每次请求新建图实例避免跨事件循环复用锁。
- `memory/checkpointer.py` 拆分为 `build_checkpointer()`（同步）/ `build_async_checkpointer()`（异步）。

### 4. 循环熔断 + 简单限流
- **循环熔断**：`plan_node` 记录 plan 签名（工具名有序拼接）到 `seen_plans`，签名重复 → 触发 deny（reason 含"循环熔断"）。`seen_plans` 由 checkpointer 跨 turn 持久化，故需从 runner 的 initial 中省略以允许合并。新增 `route_after_plan` 条件边。
- **限流**：`/api/chat` 加回 `@limiter.limit("10/minute")`，同时挂载 `SlowAPIMiddleware`（原代码未挂载故限流从未真正生效，这是修复）。通过 `APP_V4_DISABLE_RATE_LIMIT` 环境变量在测试中关闭。
- 原 `_Limiter__limit_decorator` 只注册限制规则，实际依赖 SlowAPIMiddleware 执行。

### P3 测试覆盖
- 新增 4 个测试文件：test_memory.py（11）/ test_rag.py（16）/ test_stream.py（4）/ test_ratelimit.py（4），共 35 条全绿。
- 既有的 22 条 P0-P2 测试全部仍通过。**总计 57 passed**。
