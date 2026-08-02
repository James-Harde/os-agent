# app_v4 Current Work State

Updated: 2026-08-02

This file is the single current progress source for `app_v4`. It records
accepted facts and remaining gaps, not a chronological log.

## Goal

Build a runnable and interview-ready operations Agent with a deliberately
small scope and mainstream engineering choices:

`FastAPI + LangGraph + LangChain + official MCP + real RAG + Trace/Eval + Docker Compose`

The active implementation is `app_v4`. Do not create `app_v5`, and do not
modify `app`, `app_v2`, or `app_v3`.

## Current Status

| Area | Status | Current fact |
|---|---|---|
| Agent main path | Core thin slice passed | async non-streaming `/api/chat` now awaits explicit `arun_agent()` with request-scoped Dependencies held for the full graph execution; consult, bounded read-only ReAct, real tools, Trace, and isolation pass focused regression |
| Safety and HITL | Mostly complete | dangerous-request denial, injection handling, auto/confirm/deny policy, `interrupt()` and `Command(resume=...)`, audit, and idempotent approval behavior pass the async non-streaming regression |
| MCP | Engineering PASS; transfer pending | 官方 FastMCP + Streamable HTTP；独立 Server 启动、官方 Client、`/api/chat`→MCP→真实工具 E2E、真实 DeepSeek 生产链均通过；最小权限、结构化错误、共享审计和生产 fail-fast 已有反回归测试 |
| RAG | Core thin slice passed | real Ollama Embedding → Milvus dense + BM25 + RRF → cited results independently passes; retrieval evaluation and measured bad-case work remain later delivery items |
| Streaming/performance | Implementation incomplete | `runner.py` now contains a v2 `astream(messages, updates)` attempt; basic streaming, model-error handling, and behavioral backpressure pass, but two disconnect-cancellation behaviors still fail and the attempt imports a private LangChain marker API, so it is not acceptable production evidence |
| Memory/context | Basic complete | thread checkpoints, SQLite long-term memory, user/thread isolation, TTL, and compression foundations exist |
| Delivery evidence | Partial | automated tests are broad, but README, real-service smoke evidence, and interview notes are not complete |

## Lifecycle Implementation Present; Acceptance Pending

The current tree contains the following lifecycle work, but it is not accepted
until the warning-as-error tests pass:

1. `app_v4/model/chat_model.py` — removed ALL v3 backpressure coupling
   (`_ModelStreamBackpressure`, `_BackpressureStreamHandler`, and all context
   vars/bind/reset/cancel helpers). `model_invoke_streaming` now just calls
   `await model.ainvoke(messages)`.
2. `app_v4/container.py` — added idempotent `async aclose()` that closes
   AsyncSqliteSaver's aiosqlite connection (and other async resources); `reset()`
   now closes sync checkpointer before nulling.
3. `app_v4/main.py` — added async `lifespan` to `create_app` that calls
   `deps.aclose()` on shutdown.

The independent 2026-08-02 focused run still raised
`PytestUnhandledThreadExceptionWarning: Event loop is closed` in
`TestTwoAppTwoDb::test_two_apps_independent_threads`. Therefore `aclose()`,
lifespan ownership, and `reset()` semantics remain incomplete. Result:
`2 passed, 1 failed, 1 warning in 8.18s`.

## v2 Streaming Repair Attempt (2026-08-02, Incomplete)

The current tree contains a tracked implementation attempt in
`app_v4/graph/runner.py`:

1. `graph.astream(..., version="v2", stream_mode=["messages", "updates"])`
   replaced the previous v3 event path.
2. A bounded token queue and top-level callback handler were added. Basic SSE,
   model-error handling, and the behavioral backpressure test now pass.
3. The undefined `_streaming_run_state.clear()` cleanup call was removed.
4. The handler imports and inherits
   `langchain_core.tracers._streaming._StreamingCallbackHandler`. This is a
   private API and directly violates the public-API acceptance constraint.
5. Disconnect cancellation still stops the HTTP/driver task without reaching
   the injected model's `_astream` as `CancelledError`. The private marker
   experiment did not close this behavior gap.

Independent focused command (streaming plus dependency isolation, lifecycle
excluded):

```text
14 passed, 2 failed, 1 warning in 28.13s

FAIL: real TCP disconnect does not cancel the underlying model
FAIL: cancelling stream A does not reach model A while stream B remains valid
```

This is progress from three SSE behavior failures to two, not completion.
Historical v3 results and mechanism probes are not acceptance evidence for the
current tree.

## Previous Async Non-Streaming Repair (2026-07-29)

Scope was limited to the non-streaming Agent entry and dependency-container
isolation. SSE cancellation, backpressure, and checkpointer/connection lifecycle
were explicitly not accepted in this window.

Implemented:

1. Added public `async arun_agent()`; its `Dependencies` ContextVar remains set
   across the complete awaited graph execution and is restored in `finally`.
2. Kept `run_agent()` as a sync-only adapter. It calls `asyncio.run()` only
   without a running event loop and otherwise raises with guidance to await
   `arun_agent()`; it never returns a coroutine.
3. `/api/chat` now awaits `arun_agent()`. HITL resume uses the same container
   `ainvoke_locked()` boundary.
4. Added the missing `asyncio` import and made `Dependencies.reset()` clear
   `_ainvoke_lock`.
5. Added a direct A/B model probe and concurrent two-app/two-database tests.
   The request-bound B model is called and the ambient A model remains at zero.

Final evidence:

```text
async dependency-isolation suite: 5 passed
required core regression:         58 passed, 0 failed
focused HITL regression:          5 passed
git diff --check:                 exit 0
```

The core run also emitted three aiosqlite worker-thread warnings after TestClient
event-loop shutdown. They are recorded as unresolved resource-lifecycle
evidence for the next streaming/lifecycle window, not as a failure of the
non-streaming behavior and not as accepted SSE evidence.

## Accepted Architecture

The application uses a hybrid architecture:

1. FastAPI receives and validates requests.
2. LangGraph owns workflow state, routing, checkpointing, bounded loops, and
   HITL.
3. Consultation requests go directly to an answer node.
4. Read-only diagnosis uses bounded ReAct:
   decide -> validate -> execute -> scan observation -> continue/stop.
5. Knowledge requests use the RAG tool.
6. Mutating requests use:
   plan -> policy validation -> HITL -> frozen-argument execution ->
   verification.
7. LangChain supplies model, message, tool, splitter, embedding, retriever, and
   vector-store integrations.
8. Model output is an untrusted candidate decision. The graph validates schema,
   tool allowlist, parameters, permission, risk, budget, and approval before
   execution.

## Latest Independent MCP Audit (2026-07-28, repair verified)

Verified:

```text
focused MCP tests:                 18 passed
real Streamable HTTP MCP E2E:      3 passed
real CLI server startup:           PASS (MCP_SERVER_URL empty)
real DeepSeek -> MCP -> real tool: 1 passed
default offline suite:             156 passed, 13 deselected
pip check:                         pass
git diff --check:                  exit 0
```

The 2026-07-28 independent audit found 7 MCP gaps. All seven are closed.
The post-repair re-audit then found and closed three additional blind spots:

1. **Production fail-fast**: `build_dependencies` raises `RuntimeError` when
   `use_fake_model=false` and `mcp_server_url` is empty — no silent
   `LocalToolInvoker`. `LocalToolInvoker` remains only for explicit
   test/dev injection (`use_fake_model=true`).
2. **Structured metadata**: `tools/list` returns non-null `ToolAnnotations`
   (`readOnlyHint`/`destructiveHint`/`idempotentHint`) and structured `meta`
   (`permission`, `risk_level`). Risk is no longer appended to the description.
3. **isError semantics**: known-tool validation failures, policy blocks, and
   injection blocks return `CallToolResult(isError=True, ...)` while
   preserving the JSON payload.
4. **Blocked injection audited**: the handler writes the MCP audit row *before*
   returning the blocked result, so a blocked call produces exactly one audit
   row with zero tool executions.
5. **Unified execution**: auto tools go through
   `ToolApplicationService.execute_auto()` (schema check, permission check,
   invoke, output scan) — `native_server.py` no longer calls
   `tool_obj.invoke()` directly.
6. **Unique Trace ID**: each MCP call generates a fresh UUID invocation ID
   (`mcp:<tool>:<uuid>`), so repeated identical calls get distinct trace rows.
7. **Least privilege + confirm boundary**: external MCP registers only `auto`
   read-only tools. `confirm`/`mutation` tools stay on the LangGraph
   policy → HITL → server-verified approval path; client-supplied
   `approval_status` is never trusted.
8. **Standalone startup isolation**: removed the eager module-level FastMCP
   singleton and package import side effect. Starting the Server no longer
   constructs the Web Agent MCP Client container or requires
   `MCP_SERVER_URL`.
9. **Complete MCP error semantics**: `unavailable`, `timeout`, `disabled`,
   `denied`, and `rejected` now set `isError=True`; operational failures remain
   distinct from policy blocks in audit fields.
10. **Structured disconnect**: transport/protocol failures return
    `status=unavailable`, `source=mcp_transport`, and never fall back to a local
    tool.

## RAG Decision

The old FAISS/rank-bm25/custom-adapter repair route is cancelled.

The approved production-shaped RAG path is:

- LangChain `Document`
- LangChain `RecursiveCharacterTextSplitter`
- a real embedding model with independent configuration
- Milvus Standalone in Docker Compose
- Milvus dense retrieval
- Milvus built-in BM25 sparse retrieval
- RRF fusion
- source citations
- a small versioned corpus/query/qrels evaluation set

First make this thin path run end to end. Add cross-encoder reranking, query
rewriting, or parent-child indexing only after a measured bad case proves the
need.

After the replacement passes, delete the superseded custom RAG modules,
adapters, tests, and configuration. Do not keep two production paths.

## Known Gaps

1. Stable public-v2 SSE is not accepted. The current v2 attempt passes
   behavioral backpressure but fails real TCP cancellation and A/B cancellation
   isolation, and it depends on a private LangChain marker API.
2. AsyncSqliteSaver/aiosqlite cleanup still emits a worker-thread exception
   after event-loop shutdown; resource lifecycle is not accepted.
3. RAG still needs versioned retrieval evaluation and one measured Badcase.
4. Real-model readonly ReAct is non-deterministic: the model may pick
   arguments the safety validator rejects on some runs. The pipeline handles
   this gracefully (error Observation fed back, loop continues), but a run is
   not guaranteed to show successful tool data every time. Prompt guidance
   mitigates but does not eliminate this.
5. MCP engineering acceptance passed, but the user Stage 4 teach-back remains
   before the MCP learning chain is marked complete.

## Current Evidence

| Acceptance criterion | Status |
|---|---|
| pip check | ✅ pass |
| MCP 聚焦测试 | ✅ 18 passed |
| MCP 真实 Streamable HTTP E2E | ✅ 3 passed |
| 独立 MCP CLI 启动（空 MCP_SERVER_URL） | ✅ PASS |
| 真实 DeepSeek → MCP → 真实工具 | ✅ 1 passed |
| 真实 Milvus integration 全通过 | ✅ 5 passed |
| 真实 Embedding smoke 全通过 | ✅ 2 passed |
| 自动化真实 Embedding + Milvus E2E | ✅ 1 passed (collection cleaned up) |
| 真实 DeepSeek 普通咨询 | ✅ PASS |
| 真实 DeepSeek 只读 ReAct | ✅ PASS |
| 异步依赖容器隔离（A=0，B>0） | ✅ 5 passed |
| SSE v2 真流式/断连/隔离/背压 | ❌ 14 passed, 2 cancellation failures, 1 warning |
| FastAPI + AsyncSqliteSaver 生命周期 | ❌ 2 passed, 1 warning-as-error failure |
| `/api/chat` 核心聚焦回归 | ✅ 58 passed, 0 failed |
| 默认离线回归 | ✅ 164 passed, 13 deselected, 0 failed |
| git diff --check 通过 | ✅ exit 0 |

## Infrastructure Status

Docker Desktop v29.6.2 + Compose v5.3.1 installed and working.
- Milvus Standalone v2.6.21 healthy.
- Ollama is reachable at its local HTTP endpoint and
  `qwen3-embedding:0.6b` returns 1024-dimensional vectors.
- Root `.env` exists and Chat, Embedding, and Milvus settings load successfully.
- Integration tests run against real Milvus (not skipped).
- Connection URI: `http://127.0.0.1:19530` (set via `MILVUS_URI`).

## Focused Verification Commands

Active SSE baseline:

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_stream.py app_v4/tests/test_acceptance_blackbox.py::TestG5SSETokenStream app_v4/tests/test_acceptance_blackbox.py::TestG5Cancellation app_v4/tests/test_async_dependency_isolation.py -q -p no:cacheprovider -o addopts=""
```

Deferred lifecycle gate:

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_p0_anticheat.py::TestTwoAppTwoDb -q -p no:cacheprovider -o addopts="" -W error::pytest.PytestUnhandledThreadExceptionWarning
```

MCP regression (not part of the active repair):

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_mcp.py -q -p no:cacheprovider -o addopts=""
.venv\Scripts\python -m pytest app_v4/tests/test_mcp_e2e.py -m real_mcp_e2e -q -s -p no:cacheprovider -o addopts=""
.venv\Scripts\python -m pytest app_v4/tests -q -p no:cacheprovider
```

## Next Three Actions

1. Remove the private `_StreamingCallbackHandler` dependency and establish the
   correct cancellation ownership boundary using documented public
   LangGraph/LangChain/FastAPI/Starlette APIs. Do not retry a disproven marker
   hypothesis or replace framework behavior with a hand-written imitation.
2. Close exactly the two cancellation failures while preserving the 14 passing
   focused behaviors; run the focused SSE command and `git diff --check`.
3. Update this file and `HANDOFF-LATEST.md`, then stop. Lifecycle repair is the
   next separate window, not part of the active SSE cancellation scope.

## Documentation Rules

- Current execution state: this file.
- Next-window recovery: `HANDOFF-LATEST.md`.
- Current acceptance overview: `FINAL-ACCEPTANCE-MATRIX.md`.
- Project-wide roadmap: `AGENT-CHAIN.md`.
- Market evidence: `INTERVIEW-MARKET.md`.
- Do not create additional status, audit, sprint, or acceptance Markdown files.
