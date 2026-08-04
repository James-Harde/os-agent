# app_v4 Current Work State

Updated: 2026-08-04

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
| Streaming/performance | Accepted (focused gate) | `runner.py` uses public-v2 `astream(version="v2", stream_mode=["updates"])`; the node (incl. model) runs inline in the driver task via the single-task fast path, so disconnect `CancelledError` reaches the model `_astream`. `model_invoke_streaming` passes public `stream=True` to `ainvoke` so tokens flow via `on_llm_new_token`. Private `_StreamingCallbackHandler` marker removed. Focused SSE behavioral gate 11/11 pass (4 test_stream + 7 TestG5; lock this version — contract tests cover focused behavior, internal scheduling is NOT a public contract); full offline suite 166 passed + 13 real-marker deselected (2 real-model failures are pre-existing environmental `MCP_SERVER_URL`-empty gating, unrelated) |
| Lifecycle | Accepted | FastAPI `lifespan` owns `Dependencies.aclose()`; `aclose` is idempotent and nulls all checkpointer refs. `TestTwoAppTwoDb` now wraps both apps in `with TestClient` so lifespan runs and aiosqlite worker threads are stopped — 5x clean runs under `-W error::pytest.PytestUnhandledThreadExceptionWarning`. `reset()` no longer fire-and-forget closes async resources (deleted `_close_async_checkpointer_best_effort`); sync `reset` closes the sync sqlite3 connection only, async cleanup is owned by `aclose` |
| Memory/context | Basic complete | thread checkpoints, SQLite long-term memory, user/thread isolation, TTL, and compression foundations exist |
| Delivery evidence | Partial | automated tests are broad, but README, real-service smoke evidence, and interview notes are not complete |

## Lifecycle — Accepted (2026-08-04)

The lifecycle repair is accepted. Mechanism:

1. `app_v4/container.py` — idempotent `async aclose()` closes the
   AsyncSqliteSaver aiosqlite connection (and sync sqlite3 connection, model,
   MCP invoker, RAG store) and nulls every reference. `reset()` now closes
   only the sync sqlite3 connection and nulls the async checkpointer reference
   without fire-and-forgetting an un-awaited async close (the old
   `_close_async_checkpointer_best_effort` helper is deleted). Async cleanup
   is owned by `aclose()` via FastAPI lifespan.
2. `app_v4/main.py` — async `lifespan` calls `await dependencies.aclose()` on
   shutdown. Starlette/FastAPI only run lifespan inside `with TestClient(app)`,
   so tests must enter the context manager.
3. `app_v4/tests/test_p0_anticheat.py::TestTwoAppTwoDb` — the first two tests
   now wrap both clients in `with client_a, client_b:` so lifespan runs
   `aclose()` and aiosqlite worker threads are stopped before the next test.
4. `app_v4/tests/test_async_dependency_isolation.py` — two new tests verify
   `aclose()` idempotence (repeat call is a no-op, refs nulled) and two-app
   independence (closing A leaves B's connection intact).

Evidence (2026-08-04):

```text
TestTwoAppTwoDb (5x):   3 passed, 0 failed  (under -W error::PytestUnhandledThreadExceptionWarning)
test_async_dependency_isolation.py: 7 passed (5 old + 2 new)
SSE focused gate:       18 passed (16 SSE + 2 new lifecycle)
full offline suite:     166 passed, 13 real-marker deselected, 0 failed
full + thread-warning:  166 passed, 0 failed
pip check:              pass
git diff --check:       exit 0 (only pre-existing CRLF line-ending warnings)
```

## v2 Streaming Repair (2026-08-02, Accepted on focused gate)

The repair is complete and accepted on the focused SSE gate (16/16). The
previous private-marker attempt is replaced by two public-API changes:

1. **`app_v4/graph/runner.py`** — `streaming_agent` now calls
   `graph.astream(..., version="v2", stream_mode=["updates"])` (dropped
   `"messages"`). Without `"messages"` LangGraph does not create a `get_waiter`,
   so `PregelRunner.atick` takes the single-task fast path: the node (including
   the model) runs *inline in the driver task*. Cancelling the driver therefore
   delivers `CancelledError` straight to the inline model `_astream` instead of
   it lingering in a separate node task. The private
   `_StreamingCallbackHandler` marker import and inheritance are deleted; the
   `_BackpressureHandler` is now a plain `AsyncCallbackHandler`.

2. **`app_v4/model/chat_model.py`** — `model_invoke_streaming` now passes the
   public `stream=True` kwarg to `model.ainvoke(...)`. This routes the model
   through `_astream` so each token fires `on_llm_new_token` (collected by the
   bounded `_BackpressureHandler` queue) while `ainvoke` still aggregates the
   full result. This replaces the marker's token-flow role with a documented
   public parameter; non-streaming callers with no token handler behave
   identically.

The marker had been doing double duty: the disproven `do_stream` cancellation
hypothesis AND forcing the model to stream tokens via `_should_stream`. Dropping
`"messages"` fixed cancellation; passing `stream=True` restored token flow.

Independent focused command (streaming plus dependency isolation, lifecycle
excluded):

```text
16 passed, 1 warning in 14.21s
```

Full offline suite:

```text
175 passed, 17 warnings in 141.83s

FAIL: test_real_readonly_smoke.py::test_real_readonly_react_pipeline
FAIL: test_real_readonly_smoke.py::test_real_consult_direct_answer
```

The 2 failures are pre-existing environmental gating: both raise `RuntimeError`
in `build_dependencies` (`container.py:436`) because `MCP_SERVER_URL` is empty —
before any model invocation, unrelated to streaming. `git diff --check` exit 0.

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

1. ~~Stable public-v2 SSE is not accepted~~ — ACCEPTED on focused gate
   (2026-08-02). `stream_mode=["updates"]` + inline single-task path delivers
   disconnect `CancelledError` to the model; public `stream=True` on `ainvoke`
   restores token flow; private marker removed. Remaining: real-model SSE smoke
   evidence (TTFT percentiles) is not yet measured.
2. ~~AsyncSqliteSaver/aiosqlite cleanup emitted a worker-thread exception after
   event-loop shutdown~~ — ACCEPTED (2026-08-04). Lifespan owns `aclose()`; tests
   use `with TestClient`; `reset()` no longer fire-and-forgets async close.
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
| SSE v2 真流式/断连/隔离/背压 | ✅ focused behavioral gate 16/16 pass (lock this version; internal scheduling is not a public contract) |
| FastAPI + AsyncSqliteSaver 生命周期 | ✅ 2026-08-04 accepted; 5x clean under warning-as-error |
| `/api/chat` 核心聚焦回归 | ✅ 58 passed, 0 failed |
| 默认离线回归 | ✅ 166 passed, 13 real-marker deselected, 2 environmental failures, 0 failed |
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

Lifecycle gate (accepted 2026-08-04):

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_p0_anticheat.py::TestTwoAppTwoDb app_v4/tests/test_async_dependency_isolation.py -q -p no:cacheprovider -o addopts="" -W error::pytest.PytestUnhandledThreadExceptionWarning
```

MCP regression (not part of the active repair):

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_mcp.py -q -p no:cacheprovider -o addopts=""
.venv\Scripts\python -m pytest app_v4/tests/test_mcp_e2e.py -m real_mcp_e2e -q -s -p no:cacheprovider -o addopts=""
.venv\Scripts\python -m pytest app_v4/tests -q -p no:cacheprovider
```

## Next Three Actions

1. ~~Remove the private marker and fix SSE cancellation~~ — DONE (2026-08-02).
   `stream_mode=["updates"]` + inline single-task path + public `stream=True`;
   focused gate 16/16; private marker removed from `runner.py`.
2. ~~Lifecycle repair~~ — DONE (2026-08-04). Lifespan owns `aclose()`; tests use
   `with TestClient`; `reset()` no longer fire-and-forgets async close; 5x clean
   runs under warning-as-error.
3. Real-model SSE smoke evidence: measured TTFT percentiles and end-to-end token
   stream against a live model.

## Documentation Rules

- Current execution state: this file.
- Next-window recovery: `HANDOFF-LATEST.md`.
- Current acceptance overview: `FINAL-ACCEPTANCE-MATRIX.md`.
- Project-wide roadmap: `AGENT-CHAIN.md`.
- Market evidence: `INTERVIEW-MARKET.md`.
- Do not create additional status, audit, sprint, or acceptance Markdown files.
