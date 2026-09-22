# app_v4 Latest Handoff

Updated: 2026-09-22
Status: lifecycle and SSE transport ACCEPTED; result-first SSE UI and four-route examples implemented and contract-tested, screenshot-level visual recheck pending. Next: restart FastAPI, visual smoke, then real-model SSE evidence.

## Resume

Read only:

1. `D:\klin-agent\app4-需求清单.md`
2. `app_v4/docs/WORK-STATE.md`
3. this file
4. `git status --short`
5. focused diff for `app_v4/graph/runner.py`, `app_v4/main.py`,
   `app_v4/mcp/agent_invoker.py`, and `app_v4/static/index.html`

Do not repeat a full-repository audit. Do not enter RAG, MCP, or any other
roadmap area unless the user changes scope.

## Result-First SSE UI Repair (2026-09-22)

1. `graph/runner.py` — readonly `readonly_execute` updates now become realtime
   `execute` SSE events. Terminal `done` now includes the authoritative
   `tool_calls` list as well as `answer`.
2. `static/index.html` — the final answer is always the first/default-visible
   content. Raw token streaming moved into a collapsed "模型流式过程" panel;
   tool arguments and returned data render safely in a collapsed "工具调用结果"
   panel; run steps are also collapsed. `done.answer` replaces the loading text
   even when token events were received.
3. `main.py` + `mcp/agent_invoker.py` — new read-only `GET /api/tools` endpoint
   uses the configured MCP Client's `tools/list`; explicit local test/dev adapter
   returns the same catalog shape. The frontend no longer calls removed
   `/api/mcp`, so the tool count is no longer permanently zero.
4. Evidence: result/stream/API focused suite `11 passed`; native FastMCP
   invoker call + catalog assertion `1 passed`; inline frontend JavaScript
   compiled successfully; live test server returned 8 tools and served all
   three target UI regions. The isolated port-8010 server was stopped.
5. Remaining: no browser was available through Computer Use and Playwright was
   not installed, so visually recheck desktop/mobile after restarting FastAPI.

## Router Examples and Model Status (2026-09-22)

1. Welcome examples now cover all graph routes: `consult`,
   `readonly_diagnosis`, `knowledge`, and `mutation`. Buttons use `data-prompt`,
   so route labels are presentation only and are not sent as user input.
2. Fake routing now treats general cause/principle questions as `consult` when
   they do not request current-machine inspection. Fake direct answers have a
   dedicated branch instead of reusing the tool-summary template.
3. `/api/health` exposes only non-secret `model_mode` and `model_name` fields.
   The header displays `在线 · 假模型` in fake mode or the configured model name
   in API mode; online status alone no longer implies external API use.
4. Evidence: router/readonly/SSE/frontend regression `30 passed`; focused
   health/frontend/consult stream `3 passed`; inline JavaScript syntax valid.

## Current Window (SSE realtime frontend, accepted 2026-08-04)

1. `app_v4/graph/runner.py` — `streaming_agent` 新增 `node_queue`（capacity 64）；
   `_drive_graph` 从 `astream` updates 实时发射 preflight/plan/execute/summarize/deny
   节点事件；主循环用 `asyncio.wait` 合并消费 `token_queue` + `node_queue`（token 不丢、
   进度不滞后，背压/取消语义保留）。`_terminal_events` 不再重建节点事件（避免重复）。
2. `app_v4/static/index.html` — 重做为深色高对比运维控制台：CSS 变量定义调色板
   （不再依赖 Tailwind/DaisyUI CDN）；token 用 `createTextNode` 安全渲染（模型输出
   不经 innerHTML）；步骤时间线（preflight/plan/execute）与 token 流分离，done
   不覆盖过程信息；AbortController 取消显示"已停止生成"；欢迎态 3 个可点击示例；
   右侧可折叠 Trace + 工具状态。
3. `app_v4/static/sse-parser.js` — 无依赖 SSE 纯函数（UMD，浏览器+Node 同源码）：
   `parseSSEChunk(buffer, chunk)` 处理 \r\n/\r/\n、跨 chunk、多 data 行、JSON 解析失败；
   `parseSSEFlush` 冲刷尾部。由 `tests/test_sse_parser.test.js`（13 例，node:test）
   单元测试，pytest 包装器 `test_sse_parser_pytest.py` 调度。
4. 新增 `tests/test_stream_realtime.py`（5 例）：验证 token 在 done 之前出现、
   节点事件在 done 之前出现、parser 处理真实流、done 携带统计、状态推进序列。

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

### SSE Token Rendering Bug Fix + Browser Verification (2026-08-05)

**Root cause**: `static/index.html` 第 505 行 `<script src="sse-parser.js">` 使用
相对路径。页面由 `GET /` 服务（`FileResponse`），浏览器把相对路径解析为
`/sse-parser.js`（404），而静态文件挂在 `/static`。结果 `SSEParser` 未定义，
SSE 流解析抛出 ReferenceError，前端 fallback 到"失败"状态——token 永不渲染。

**Fix**: `src="sse-parser.js"` → `src="/static/sse-parser.js"`（1 字符级改动）。

**Browser verification**（Playwright + Chromium，真实服务器 `APP_V4_USE_FAKE_MODEL=true`）：
- 桌面 1440×900：欢迎态（3 示例可点击）→ 流式（token 在 t=0 即渲染，步骤卡
  绿色左边框）→ 完成（162 tokens / TTFT 47.86ms / 状态"已完成"）
- 窄屏 390×844：单列布局，可读，输入框固定底部
- 取消：状态明确显示"已停止生成"，界面恢复可输入
- `SSEParser` 对象确认加载；DOM 中 `.answer-text` 在 done 前已有内容
- WCAG 对比度：主文字/背景 16.26、次要 8.86、成功/警告/危险均 ≥4.78（AA 通过）

**Test fix**: `test_sse_parser_pytest.py` 的 `subprocess.run` 调用加
`encoding="utf-8", errors="replace"`，消除 Windows GBK 管道解码 warning
（Node 子进程中文输出被管道按 GBK 读导致的 `PytestUnhandledThreadExceptionWarning`）。

截图证据：`D:/klin-agent/tmp_sse/shots/final_*.png`（7 张）。

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

Restart the FastAPI process, submit `分析磁盘`, and visually confirm the final
answer is immediately visible while model/tool/step details are collapsed and
expand correctly. Then continue real-model SSE TTFT evidence. RAG and MCP
engineering are not part of that next window.

## Next Three Actions

1. ~~Replace the private-marker experiment with a public mechanism~~ — DONE
   (2026-08-02). `stream_mode=["updates"]` + inline single-task path + public
   `stream=True`; focused gate 11/11.
2. ~~Lifecycle repair~~ — DONE (2026-08-04). Lifespan owns `aclose()`; tests
   use `with TestClient`; `reset()` no longer fire-and-forgets async close; 5x
   clean runs under warning-as-error.
3. Result-first UI visual smoke, followed by real-model SSE TTFT evidence.

Keep at most five TODO items. After each accepted behavior, after about 20 tool
calls or 45 minutes, and before context reaches 70%, checkpoint exact facts in
the two durable documents. Test one hypothesis once; if evidence contradicts
it, record and discard it instead of retrying it. If the same blocker repeats
three times, run the smallest relevant test, update both documents, and end
honestly as BLOCKED/INCOMPLETE so a fresh context can resume immediately.
