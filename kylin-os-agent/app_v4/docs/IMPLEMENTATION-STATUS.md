# app_v4 实现状态追踪

> 创建日期：2026-07-20
> 任务：按 CC-APPV4-REPAIR-PROMPT.md 完成 A-G 阶段深度修复
> 基线：66 passed（62 原始 + 4 新增 F1/F3 测试）

---

## 当前已知缺陷（来自审计基线，逐项修复）

| # | 缺陷 | 状态 | 修复阶段 |
|---|---|---|---|
| 1 | 测试断言弱（"答案非空"、串行伪装并发、confirm 无有效断言） | ✅ 已修复+回归测试 | A |
| 2 | fake model 扫描整个 prompt 含工具名，"你好"→disk_usage | ✅ 已修复（只从 user_input 提取意图） | A |
| 3 | thread 内上轮 intent/plan/tool_calls/guard 污染下轮 | ✅ 已修复（initial 显式重置） | B |
| 4 | 循环签名只按工具名排序，忽略参数/顺序 | ✅ 已修复（签名含参数 JSON） | B |
| 5 | confirm 工具不进规划候选，审批无真实 interrupt/resume | 待修复 | C |
| 6 | 流式路径不写审计/Trace/记忆，run_id 查 Trace 404 | ✅ 已修复（streaming_agent 补写审计+记忆） | B |
| 7 | SSE 是节点事件非 token 流，无 TTFT/取消/背压 | 待修复 | F |
| 8 | "分析含 rm -rf 的日志" 触发无关磁盘工具 | ✅ 已修复（fake model 只看 user_input） | A/C |
| 9 | 恶意工具输出只写提示，summarizer 可原样输出攻击指令 | ✅ 已修复（scan_final_answer 确定性阻断） | C |
| 10 | disk_usage 根目录算到 D:\klin-agent 而非项目根 | ✅ 已修复（parents[2]） | B |
| 11 | MCP 是进程内自制 JSON-RPC，无标准 initialize/transport | 待修复 | D |
| 12 | RAG 向量实为 TF-IDF，评测 8 条且答案泄漏 | 待修复 | E |
| 13 | 短期记忆无 AIMessage/ToolMessage，长期记忆按 thread_id | 待修复 | G |
| 14 | 限流只覆盖部分接口，无算法证据/预算熔断/kill switch | 待修复 | F |
| 15 | README 对 create_agent 描述失真 | 待修复 | G |
| 16 | .venv 缺 LangGraph，依赖环境不可复现 | 待修复 | A |
| 17 | .env 可能已被 Git 跟踪 | 待报告 | A |

---

## 阶段进度

### 阶段 A：基线、环境与测试可信度 — ✅ 完成

**范围**：建立可信测试环境，修复 fake model 误判，补回归测试复现关键缺陷。

**已完成的修复**：
- [x] F1: port_lookup 正则修复（`(?<=[:.]){port}(?!\d)`）
- [x] F3: mcp_endpoint 补 logger 区分异常类型
- [x] 修复 fake model 只从 user_input 提取意图（audit #2）
- [x] 修复 _safe_path 根目录 parents[2]（audit #10）
- [x] 补回归测试：test_greeting_uses_no_tools、test_disk_analysis_calls_disk_usage_exactly

### 阶段 B：P0 主链路、状态隔离、真实只读工具 — ✅ 完成

**已完成的修复**：
- [x] 修复状态污染：initial 显式重置 intent/plan/tool_calls/guard（audit #3）
- [x] 修复循环签名含参数 JSON（audit #4）
- [x] 修复流式路径写审计+记忆（audit #6）
- [x] 补回归测试：test_state_isolation_between_rounds、test_different_requests_no_false_loop、test_stream_run_id_queryable_trace

### 阶段 C：P0 安全、真实 HITL、全路径 Trace — ✅ 核心完成

**已完成的修复**：
- [x] 修复分析语境零工具（audit #8，通过 fake model 修复间接解决）
- [x] 新增 scan_final_answer 确定性输出阻断（audit #9）
- [x] 补回归测试：test_analysis_context_zero_tools、test_output_guard_blocks_malicious_answer

**待完成（需更多时间）**：
- [ ] confirm 工具真实 interrupt/resume（audit #5）

### 阶段 D：P1 标准 MCP — ⚠️ 部分完成

**已完成的修复**：
- [x] 安装官方 MCP SDK（mcp==1.28.1）
- [x] 固定 starlette 版本避免与 fastapi 冲突

**待完成（需更多时间）**：
- [ ] 用官方 SDK 重写 server.py（标准 initialize 生命周期 + 真实 transport）
- [ ] MCP Client 通过 transport 调用（非直接实例化 server）
- [ ] MCP 调用走统一审计

### 阶段 E：P1 真正 RAG — ⏳ 待开始

**待完成**：
- [ ] 独立语料 + 版本化 qrels 评测集（20-30 条）
- [ ] 真实 embedding 稠密召回（当前为 TF-IDF）
- [ ] 父子索引、双路融合、查询改写
- [ ] MRR/nDCG 指标 + Badcase 前后对比

### 阶段 F：P2 性能、预算、缓存 — ⏳ 待开始

### 阶段 G：P2 记忆、上下文与交付证据 — ✅ 部分完成

**已完成的修复**：
- [x] 重写 README create_agent 对比（audit #15，承认 create_agent 是标准 API）
- [x] 锁定依赖版本 requirements-v2.txt（audit #16）
- [x] .env 加入 .gitignore（audit #17）

**待完成**：
- [ ] 短期记忆保存 AIMessage/ToolMessage
- [ ] 长期记忆跨 thread 检索（当前按 thread_id）
- [ ] 记忆过期/纠错/删除/压缩/污染防护

---

## 关键决策记录

| 决策 | 理由 |
|---|---|
| 修复 fake model 而非替换 | fake model 是测试基础设施，应只 mock 模型边界 |
| port_regex 用正向后顾 `(?<=[:.])` | 兼容所有 netstat 格式同时避免 80800 误判 |
| 不删文件、不改 app_v2/v3/app | 遵守任务边界 |

---

## 残余风险

- **Phase D（标准 MCP）**：SDK 已安装，但重写 server/client + transport + 集成测试需数小时工程时间，且 starlette 版本冲突需持续关注
- **Phase E（真正 RAG）**：需要真实 embedding 模型/服务（如 OpenAI embedding API 或本地 sentence-transformers），当前 TF-IDF 无法冒充稠密向量
- **Phase F（性能/预算/缓存）**：限流算法实现 + 预算熔断 + 缓存策略需额外工程时间
- **.env 已在 Git 索引中**：已加入 .gitignore 防止新提交，但历史提交仍含 .env 内容，推送前需额外处理（git rm --cached 或 BFG）
- **短期/长期记忆**：当前长期记忆按 thread_id 维度存储，跨 thread 检索和记忆过期/纠错/删除未实现
- **SSE 仍是节点事件**：无真实 token 级流、TTFT、取消传播、背压

## 测试通过数

- **最终：73 passed**（基线 62 → +11 新增回归测试）
- 解释器：Python 3.13.9（系统 Conda）
- 核心依赖：langgraph==1.2.9, fastapi==0.111.0, mcp==1.28.1
- 命令：`python -m pytest app_v4/tests/ -v`
- 耗时：~5 秒
- 无不明 skip，无新增 warning（仅 starlette multipart 第三方 PendingDeprecationWarning）
