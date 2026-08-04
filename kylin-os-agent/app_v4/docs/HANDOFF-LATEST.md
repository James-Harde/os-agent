# app_v4 Latest Handoff

Updated: 2026-08-04
Status: lifecycle ACCEPTED (2026-08-04); SSE ACCEPTED (focused gate 16/16, locked)

## Resume

Read only:

1. `D:\klin-agent\app4-需求清单.md`
2. `app_v4/docs/WORK-STATE.md`
3. this file
4. `git status --short`
5. focused diff for `container.py`, `main.py`, `test_p0_anticheat.py`

Do not repeat a full-repository audit. Do not enter RAG, MCP, or any other
roadmap area; SSE is locked (focused behavioral gate only — internal scheduling
is NOT a public contract).

## Current Window (lifecycle, accepted 2026-08-04)

1. `container.py` — `Dependencies.reset()` no longer fire-and-forget closes the
   async checkpointer. The old `_close_async_checkpointer_best_effort` helper is
   deleted. Sync `reset` closes only the sync sqlite3 connection and nulls the
   async checkpointer reference; async cleanup is owned by `aclose()`. `aclose()`
   remains idempotent and nulls all checkpointer refs.
2. `main.py` — async `lifespan` calls `await dependencies.aclose()` on shutdown.
   Starlette/FastAPI only run lifespan inside `with TestClient(app)`.
3. `test_p0_anticheat.py::TestTwoAppTwoDb` — the first two tests now wrap both
   clients in `with client_a, client_b:` so lifespan runs `aclose()` and the
   aiosqlite worker threads are stopped before the next test (the flaky
   `PytestUnhandledThreadExceptionWarning: Event loop is closed` is gone — 5x
   clean runs).
4. `test_async_dependency_isolation.py` — two new tests verify `aclose()`
   idempotence and two-app independence (closing A leaves B's connection intact).

The window ran no destructive Git command. The current tree remains dirty with
accumulated work from earlier accepted chains; do not revert unrelated changes.

Untracked root probes remain deletion candidates (do not delete without user
approval): `probe_v2.py`, `probe_cancel.py`, `probe_cancel2.py`,
`probe_cancel3.py`, `probe_stream.py`, `probe_real.py`, `probe_design.py`.

## Verified Mechanism Facts

1. Starlette/FastAPI only run lifespan (and thus `aclose()`) inside
   `with TestClient(app)`. Tests that skip the context manager leak the
   AsyncSqliteSaver aiosqlite worker thread into the next test — that was the
   root cause of the flaky `PytestUnhandledThreadExceptionWarning`.
2. `Dependencies.aclose()` is idempotent: every close is guarded by a None
   check and the reference is nulled after close, so repeat calls are no-ops.
3. `Dependencies.reset()` is now sync-only on the sync connection. It does NOT
   schedule, poll, sleep, or swallow async resource errors. Async lifecycle is
   fully owned by `aclose()` via lifespan.
4. Two apps hold independent `AsyncSqliteSaver` instances; closing one does not
   touch the other's connection or worker thread.

## Current Production State

- `streaming_agent()` uses public-v2 `astream(version="v2",
  stream_mode=["updates"])` with a bounded token queue and a driver task.
  No private LangGraph/LangChain APIs; `_BackpressureHandler` is a plain
  `AsyncCallbackHandler`.
- `model_invoke_streaming` (chat_model.py) passes public `stream=True` to
  `model.ainvoke(...)`; tokens flow via `on_llm_new_token`, full result still
  aggregated.
- TCP disconnect cancels the underlying model (verified on focused gate).
- Stream A cancellation reaches model A while stream B remains valid.
- Behavioral backpressure and model-error handling pass.
- FastAPI `lifespan` owns `Dependencies.aclose()`; shutdown is clean and
  warning-as-error free.

## Fresh Baseline

Lifecycle gate (accepted 2026-08-04):

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_p0_anticheat.py::TestTwoAppTwoDb app_v4/tests/test_async_dependency_isolation.py -q -p no:cacheprovider -o addopts="" -W error::pytest.PytestUnhandledThreadExceptionWarning
```

Result:

```text
10 passed, 1 warning
```

SSE focused behavioral gate (locked version; 4 test_stream + 7 TestG5 = 11):

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_stream.py app_v4/tests/test_acceptance_blackbox.py::TestG5SSETokenStream app_v4/tests/test_acceptance_blackbox.py::TestG5Cancellation -q -p no:cacheprovider -o addopts=""
```

Result:

```text
11 passed, 1 warning
```

Combined behavioral regression (SSE 11 + async-isolation 7 = 18):

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_stream.py app_v4/tests/test_acceptance_blackbox.py::TestG5SSETokenStream app_v4/tests/test_acceptance_blackbox.py::TestG5Cancellation app_v4/tests/test_async_dependency_isolation.py -q -p no:cacheprovider -o addopts=""
```

Result:

```text
18 passed, 1 warning
```

Full offline suite:

```text
166 passed, 13 real-marker deselected, 0 failed

(real_chat-marker failures: test_real_readonly_smoke.py::test_real_readonly_react_pipeline
                              test_real_readonly_smoke.py::test_real_consult_direct_answer)
```

The 2 failures are pre-existing environmental gating (empty `MCP_SERVER_URL`
raises in `build_dependencies`, `container.py:410`, before any model call) and
are unrelated to lifecycle or streaming. `git diff --check` exit 0 (only
pre-existing CRLF line-ending warnings).

## Architecture Constraints

- Prefer documented, mature LangGraph/LangChain/FastAPI/Starlette mechanisms.
  Do not hand-write a replacement for framework orchestration or cancellation.
- Production must use public stable APIs. Private modules, underscore-prefixed
  protocols, monkey patches, polling, and test-tuned timing are not acceptable.
- Production streaming uses public v2 `astream(version="v2",
  stream_mode=["updates"])` plus public `stream=True` on `model.ainvoke(...)` —
  both documented public parameters. No private APIs. Internal scheduling is a
  focused-behavior PASS only, NOT a public contract.
- A bounded async channel is accepted only where behavioral tests prove it.
- Do not use private LangGraph APIs, v3 as the final path, unbounded queues,
  polling, daemon workers, or manual final-answer tokenization.
- Cancellation must stop and await graph/model tasks, emit no `done`, write
  exactly one cancel Trace, clear the run registry, and isolate stream B.
- `Dependencies.aclose()` must be awaited and idempotent. Lifespan owns
  shutdown; `reset()` must not discard live resources or fire-and-forget.
- Do not weaken or delete tests to manufacture PASS.
- Do not use `git checkout/restore/reset/clean/stash`, and do not commit,
  push, delete files, or modify secrets without user approval.

## First Action Next Window

Real-model SSE smoke evidence: measured TTFT percentiles and end-to-end token
stream against a live model. RAG and MCP are explicitly not part of the next
window; lifecycle is accepted and locked.

## Next Three Actions

1. ~~Replace the private-marker experiment with a public mechanism~~ — DONE
   (2026-08-02). `stream_mode=["updates"]` + inline single-task path + public
   `stream=True`; focused gate 11/11.
2. ~~Lifecycle repair~~ — DONE (2026-08-04). Lifespan owns `aclose()`; tests
   use `with TestClient`; `reset()` no longer fire-and-forgets async close; 5x
   clean runs under warning-as-error.
3. Real-model SSE smoke evidence: measured TTFT percentiles and end-to-end token
   stream against a live model.

Keep at most five TODO items. After each accepted behavior, after about 20 tool
calls or 45 minutes, and before context reaches 70%, checkpoint exact facts in
the two durable documents. Test one hypothesis once; if evidence contradicts
it, record and discard it instead of retrying it. If the same blocker repeats
three times, run the smallest relevant test, update both documents, and end
honestly as BLOCKED/INCOMPLETE so a fresh context can resume immediately.
