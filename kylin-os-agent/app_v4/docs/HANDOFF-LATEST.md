# app_v4 Latest Handoff

Updated: 2026-08-02
Status: public-v2 SSE cancellation repair INCOMPLETE; lifecycle deferred

## Resume

Read only:

1. `D:\klin-agent\app4-需求清单.md`
2. `app_v4/docs/WORK-STATE.md`
3. this file
4. `git status --short`
5. focused diff for `runner.py` and the streaming/cancellation tests

Do not repeat a full-repository audit. Do not enter RAG, MCP, lifecycle, or any
other roadmap area in this window.

## Current Window

The latest window implemented a v2 streaming attempt in `runner.py`, fixed the
undefined cleanup call, and reduced the focused SSE behavior failures from
three to two. It did not meet the goal and did not update the durable handoff
before its context filled.

The window ran no destructive Git command. The current tree remains dirty with
accumulated work from earlier accepted chains; do not revert unrelated changes.

Seven untracked root probes were created:

- `probe_v2.py`
- `probe_cancel.py`
- `probe_cancel2.py`
- `probe_cancel3.py`
- `probe_stream.py`
- `probe_real.py`
- `probe_design.py`

They are temporary deletion candidates, not production evidence. Do not delete
them without user approval.

## Verified Mechanism Facts

1. Public `graph.astream(..., version="v2",
   stream_mode=["messages", "updates"])` emits the expected public v2 events
   on the real compiled graph.
2. The current bounded token channel passes the existing behavioral
   backpressure test and model-error test.
3. Real TCP disconnect cancels the HTTP/driver path but still does not reach
   the injected model's `_astream` as `CancelledError`.
4. Stream B remains independently valid, but disconnecting stream A still does
   not cancel model A.
5. Making `_BackpressureHandler` inherit the private
   `_StreamingCallbackHandler` marker did not close either cancellation gap.
   That hypothesis is disproven and must not be retried.
6. `cleanup_all_runs()` no longer references undefined
   `_streaming_run_state`.

## Current Production State

- `streaming_agent()` now attempts public-v2 `astream(messages, updates)` with
  a bounded token queue and a driver task.
- The handler imports
  `langchain_core.tracers._streaming._StreamingCallbackHandler`, a private API.
  The current attempt therefore violates its own production constraint.
- TCP disconnect does not cancel the underlying model.
- Stream A cancellation does not reach model A in the A/B isolation case.
- Behavioral backpressure and model-error handling pass the focused tests.
- AsyncSqliteSaver shutdown still leaks an aiosqlite worker-thread exception.
- Lifecycle remains a separate, deferred repair after SSE cancellation.

Historical `11 passed` results belong to an earlier working-tree snapshot and
are not evidence for the current tree.

## Fresh Baseline

Command:

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_stream.py app_v4/tests/test_acceptance_blackbox.py::TestG5SSETokenStream app_v4/tests/test_acceptance_blackbox.py::TestG5Cancellation app_v4/tests/test_async_dependency_isolation.py -q -p no:cacheprovider -o addopts=""
```

Result:

```text
14 passed, 2 failed, 1 warning in 28.13s
```

Failures:

1. `test_tcp_disconnect_reaches_underlying_astream`
2. `test_disconnect_a_does_not_cancel_independent_stream_b`

No full-suite or `pip check` acceptance was run in this repair window.
`git diff --check` exited 0.

Deferred lifecycle command independently produced:

```text
2 passed, 1 failed, 1 warning in 8.18s
FAIL: TestTwoAppTwoDb::test_two_apps_independent_threads
      (PytestUnhandledThreadExceptionWarning: Event loop is closed)
```

## Architecture Constraints

- Prefer documented, mature LangGraph/LangChain/FastAPI/Starlette mechanisms.
  Do not hand-write a replacement for framework orchestration or cancellation.
- Production must use public stable APIs. Private modules, underscore-prefixed
  protocols, monkey patches, polling, and test-tuned timing are not acceptable.
- If a new mainstream dependency is genuinely required, report its package,
  version, purpose, official source, and alternatives, then wait for user
  approval before installation.
- Production streaming must retain public v2 `astream(messages, updates)` or a
  better documented public framework path justified by evidence.
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

Read the installed package versions and relevant public implementation/docs,
then identify which task actually owns model generation and which public
FastAPI/LangGraph cancellation boundary can cancel and await it. Remove the
private marker experiment before claiming a solution.

## Next Three Actions

1. Replace the private-marker experiment with a documented public mechanism.
2. Pass exactly the two failing cancellation tests while preserving all 14
   currently passing focused behaviors; run `git diff --check`.
3. Update this handoff and `WORK-STATE.md` with commands and exact results, then
   stop. Do not begin lifecycle repair in the same window.

Keep at most five TODO items. After each accepted behavior, after about 20 tool
calls or 45 minutes, and before context reaches 70%, checkpoint exact facts in
the two durable documents. Test one hypothesis once; if evidence contradicts
it, record and discard it instead of retrying it. If the same blocker repeats
three times, run the smallest relevant test, update both documents, and end
honestly as BLOCKED/INCOMPLETE so a fresh context can resume immediately.
