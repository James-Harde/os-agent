# app_v4 Latest Handoff

Updated: 2026-07-28 (MCP repair verified → MOSTLY COMPLETE)

## Resume

Read:

1. `D:\klin-agent\app4-需求清单.md`
2. `app_v4/docs/WORK-STATE.md`
3. this file
4. `git status --short` and the focused diff

Do not repeat a full-repository audit.

## This Window Summary

MCP 已从 PARTIAL 修到可验证 PASS。基于 2026-07-28 独立审计的 7 个阻塞项全部
闭环：

1. **生产 fail-fast**：`use_fake_model=false` + 空 `mcp_server_url` 时
   `build_dependencies` 抛 `RuntimeError`，不再静默 `LocalToolInvoker`。
2. **结构化 metadata**：`tools/list` 返回非空 `ToolAnnotations` + `meta`
   （permission/risk_level），风险不再拼进 description。
3. **isError 语义**：已知工具校验/策略/注入失败返回
   `CallToolResult(isError=True, ...)`。
4. **注入阻断写审计**：阻断前先 `record()`，0 执行 +1 审计。
5. **统一执行**：auto 工具经 `ToolApplicationService.execute_auto()`，
   `native_server.py` 不再直接 `tool_obj.invoke()`。
6. **唯一 Trace ID**：每次调用生成 UUID `mcp:<tool>:<uuid>`。
7. **最小权限**：外部 MCP 仅注册 auto 只读工具；confirm/mutation 保留在
   LangGraph policy → HITL → 服务端审批链。

Verified results (project `.venv`, Python 3.13.9):

```text
default offline suite:             153 passed, 13 deselected, 0 failed
real Milvus integration:           5 passed
real embedding smoke:              2 passed
real Embedding + Milvus E2E:       1 passed (isolated collection, cleaned up)
real DeepSeek consult:             PASS
real DeepSeek read-only ReAct:     PASS (full 3-iteration loop)
MCP 官方 Client lifecycle:         1 passed (initialize + tools/list + tools/call)
MCPToolInvoker 生产路径:           1 passed (streamable_http → 真实 disk_usage)
MCP E2E (/api/chat → MCP):         1 passed
MCP 结构化 metadata + 共享审计:     1 passed (新增)
MCP mutation 不暴露 + isError:     2 passed (新增)
MCP 注入阻断 + 审计:               1 passed (新增)
MCP 唯一 invocation ID:            1 passed (新增)
MCP 断连 fail-closed:              1 passed
pip check:                         pass
git diff --check:                  exit 0
```

## Scope Decisions (still in force)

- Use public MCP SDK 1.28.1 APIs. Do not reintroduce hand-written JSON-RPC,
  duplicate transports, private `_tool_manager` access, or string-parsed risk
  metadata.
- `LocalToolInvoker` may remain only as an explicitly injected test/development
  adapter (`use_fake_model=true`).
- External MCP exposes only read-only `auto` tools. Mutation tools remain on the
  LangGraph policy -> HITL -> server-verified approval path. Never accept a
  client-supplied `approval_status` as authorization.
- All MCP outcomes pass through one injectable execution/audit boundary
  (`ToolApplicationService` + shared `AuditLogger`) and receive a unique
  invocation ID.

## Acceptance Items

| Criterion | Status |
|---|---|
| 官方 Client initialize + tools/list + tools/call | ✅ 1 passed |
| /api/chat → MCP → 真实 disk_usage E2E | ✅ 1 passed |
| 旧手写 MCP 路径已删除 | ✅ |
| MCP 断连 fail-closed | ✅ 1 passed |
| 生产默认 fail-fast（空 MCP_SERVER_URL） | ✅ 代码实现 + 注入测试验证 |
| 结构化 risk metadata / annotations | ✅ 1 passed (new) |
| known-tool `isError` 语义 | ✅ 1 passed (new) |
| MCP 注入阻断写审计（0 执行 +1 审计） | ✅ 1 passed (new) |
| MCP auto 工具统一执行服务 | ✅ execute_auto 实现 + 测试 |
| 相同调用独立 Trace ID | ✅ 1 passed (new) |
| MCP 审计与 Agent 共享 AuditLogger | ✅ 1 passed (new) |
| mutation 工具不暴露 | ✅ 1 passed (new) |
| 默认离线回归 | ✅ 153 passed, 13 deselected, 0 failed |
| git diff --check 通过 | ✅ exit 0 |

## Files Changed This Window

```
app_v4/tools/application.py       + execute_auto 统一 auto 工具执行
app_v4/mcp/native_server.py       重写：最小权限、annotations+meta、isError、唯一 ID、共享审计
app_v4/container.py               + 生产 fail-fast（空 MCP_SERVER_URL）
app_v4/tests/test_mcp.py          + 6 个新测试（metadata/isError/注入/唯一 ID/最小权限）
app_v4/tests/test_mcp_e2e.py      + 1 个新 E2E（结构化 metadata + 共享审计）
.env.example                      + MCP_SERVER_URL
docs 同步更新（WORK-STATE/HANDOFF/MATRIX）
```

## First Commands (regression guard)

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_mcp.py -q -p no:cacheprovider -o addopts=""
.venv\Scripts\python -m pytest app_v4/tests/test_mcp_e2e.py -m real_mcp_e2e -q -p no:cacheprovider -s
.venv\Scripts\python -m pytest app_v4/tests -q -p no:cacheprovider
.venv\Scripts\python -m pip check
git diff --check
```

## Next Three Actions

1. Verify and repair real server-side SSE cancellation/backpressure.
2. Complete RAG evaluation/Badcase evidence.
3. Final interview transfer and user teach-back.
