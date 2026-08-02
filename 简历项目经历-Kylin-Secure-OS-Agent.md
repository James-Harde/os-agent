# app_v4 最终成品目标版简历

> 这是“先定义最终成品，再倒推实现”的目标口径。正文按全部验收完成后的状态书写；文末指标是后续修复必须交付的证据。

**Kylin Secure OS Agent｜麒麟安全运维智能体平台**　**2026.07—至今**　**岗位：Agent / LLM 应用开发工程师（个人项目）**

**项目背景：** 面向 Linux/麒麟 OS 运维中知识分散、故障排查依赖人工经验及高风险变更缺少统一授权与追溯的问题，构建覆盖自然语言咨询、知识检索、只读诊断和变更审批的安全运维 Agent；通过分离模型决策与执行权限，实现工具调用可控、任务过程可追踪、长任务可中断恢复。

**技术架构：** Python、FastAPI、Pydantic、LangGraph、LangChain、DeepSeek、MCP Python SDK / FastMCP、Streamable HTTP、Milvus、Ollama / Qwen3 Embedding、BM25、RRF、SQLite / AsyncSqliteSaver、SSE、Docker Compose、Pytest

**主要职责：**

1. **混合 Agent 编排：** 基于 LangGraph StateGraph 设计咨询直答、RAG 知识检索、bounded ReAct 只读诊断及 `Plan → Policy → HITL → Execute → Verify` 变更链路；对模型提出的候选动作执行 Schema、白名单、参数、权限、风险与预算校验，并通过循环、重复状态、无进展、工具次数和超时熔断防止长任务失控。
2. **安全护栏与 HITL：** 实现危险请求与“分析危险文本”的语境区分、Prompt Injection 与不可信工具输出扫描、`auto / confirm / deny` 三级权限；使用 LangGraph `interrupt()`、checkpoint 与 `Command(resume=...)` 完成审批暂停、冻结参数恢复、拒绝取消和并发重复恢复幂等，配套结构化 Trace 与审计日志。
3. **官方 MCP 工具协议：** 使用 FastMCP 与 Streamable HTTP 构建独立 MCP Server/Client，打通 `/api/chat → MCP → 真实系统工具`；统一工具 Schema、权限/风险元数据、`isError` 错误语义、唯一调用 ID、安全策略和审计服务，并以最小权限、fail-fast 与 fail-closed 阻止越权暴露及本地静默回退。
4. **真实混合 RAG 与评测：** 构建 `Document → Splitter → Embedding → Milvus Dense + BM25 → RRF → Citation` 链路，实现稳定 Chunk ID 幂等入库、集合 Schema/向量维度校验及来源引用；建设版本化 corpus/query/qrels、Recall@k/MRR/nDCG 评测与 Badcase 回归，用实测结果决定 chunk、top-k、rerank 和查询改写策略。
5. **上下文与后端工程：** 设计 thread checkpoint、跨 thread 长期记忆、TTL、冲突纠正、记忆污染隔离和选择性上下文压缩；基于 FastAPI 应用工厂、依赖注入和 lifespan 管理异步资源，完成稳定 v2 SSE、TTFT/总延迟埋点、客户端取消传播、bounded-channel 背压、限流、缓存及 Docker Compose 一键部署，并建设 unit/integration/e2e/real-smoke 分层测试。

**项目成效：**

- 建立 **50 条版本化 Agent 场景集**，端到端任务成功率达到 **90%+**、路由与工具选择准确率达到 **95%+**；另设 **20 条高危/注入对抗样本**，漏拦截为 **0**，未经审批的副作用执行为 **0**。
- 建立 **50 条冻结 RAG query/qrels**，混合检索达到 **Recall@5 ≥ 90%、MRR@5 ≥ 0.80、nDCG@5 ≥ 0.85**；完成至少 **2 个可复现 Badcase**，对应失败切片指标提升 **20 个百分点以上**，整体 Recall@5 回退不超过 **2 个百分点**。
- 在本地固定负载环境完成 **20 并发、200 次 SSE 请求**，请求成功率达到 **99%+**；执行 **100 次客户端断连测试**，底层任务取消成功率 **100%**、资源释放 p95 **≤ 1 秒**，队列峰值不超过配置容量。
- 在 **20 组长对话标注集**上将上下文 Token 数降低 **50%+**、关键事实保留率达到 **95%+**；完成 **100 对并发 thread 隔离测试**，跨会话消息、工具结果和长期记忆泄漏为 **0**。
- Docker Compose 在干净环境下完成 **10/10 次启动与 readiness 验证**；真实 DeepSeek、MCP、Embedding、Milvus E2E 全部通过，最终离线、集成和端到端测试 **0 failed**，应用关闭阶段无未处理任务或资源泄漏告警。

## 这份目标版对应的 app_v4 硬验收（不放入简历）

1. **SSE/生命周期：** 稳定 LangGraph v2 真流式、真实 TCP 断连取消、A/B 流隔离、bounded-channel 背压和 AsyncSqliteSaver 资源回收全部通过 warning-as-error 测试。
2. **RAG Eval：** 修通 query/qrels → 检索 → Recall/MRR/nDCG 的可重复评测，保存 Dense 基线、混合检索结果及至少两个 Badcase 前后对比。
3. **Agent Eval：** 建立覆盖咨询、RAG、只读 ReAct、工具失败、注入、HITL 和预算熔断的版本化场景集，生成可追溯评测报告。
4. **Memory/Context：** 完成长对话压缩、事实保真、过期/冲突/用户纠正和记忆污染回归，不只验证“能存取”。
5. **部署与工程：** Docker Compose 覆盖 App、MCP、Milvus 及依赖服务，补齐 readiness、干净环境安装、真实服务 Smoke、性能报告和关闭阶段资源审计。
6. **最终投递闸门：** 简历中的所有数字必须由最终实测报告替换或确认；任何未通过的指标不得以完成式口径正式投递。
