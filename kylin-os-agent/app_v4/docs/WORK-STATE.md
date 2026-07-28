# app_v4 Current Work State

Updated: 2026-07-28

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
| Agent main path | Core thin slice passed | `/api/chat` consult and bounded read-only ReAct independently pass with real DeepSeek; real tools, Trace, thread isolation, and valid `AIMessage.tool_calls` → `ToolMessage` protocol are covered |
| Safety and HITL | Mostly complete | dangerous-request denial, injection handling, auto/confirm/deny policy, `interrupt()` and `Command(resume=...)`, audit, and idempotent approval behavior exist |
| MCP | Mostly complete | 官方 FastMCP + Streamable HTTP 只读链路和 `/api/chat`→MCP→真实 disk_usage E2E 通过；生产配置为空时 fail-fast（不再静默 LocalToolInvoker）；tools/list 返回结构化 annotations+meta（permission/risk_level）；已知工具校验/策略/注入失败符合 isError 语义；auto 工具统一经 ToolApplicationService；注入阻断写审计（0 执行 +1 审计）；每次调用唯一 invocation ID；MCP 审计与 Agent 共享可注入 AuditLogger；外部 MCP 仅暴露 auto 只读工具（最小权限） |
| RAG | Core thin slice passed | real Ollama Embedding → Milvus dense + BM25 + RRF → cited results independently passes; retrieval evaluation and measured bad-case work remain later delivery items |
| Streaming/performance | Partial | SSE, TTFT fields, token bucket, cache, budgets, and kill switch exist; real server-side cancellation/backpressure still needs verification and repair |
| Memory/context | Basic complete | thread checkpoints, SQLite long-term memory, user/thread isolation, TTL, and compression foundations exist |
| Delivery evidence | Partial | automated tests are broad, but README, real-service smoke evidence, and interview notes are not complete |

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
focused MCP tests:                 15 passed
real Streamable HTTP MCP E2E:      3 passed
default offline suite:             153 passed, 13 deselected
pip check:                         pass
git diff --check:                  exit 0
```

The 2026-07-28 independent audit found 7 MCP gaps. All seven are now closed
by code changes in this window:

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

## This Round: Completed Modifications (2026-07-27, continued — two real-chain blockers)

### Files changed (8 files, +523 / -163)

```
.env.example                           |  ++-  (removed invalid deepseek-embed template)
app_v4/docs/HANDOFF-LATEST.md          |  +----  (this doc)
app_v4/docs/WORK-STATE.md              |  +----  (this doc)
app_v4/rag/milvus_store.py             |  ++++  (core rewrite)
app_v4/settings.py                     |  +-    (removed "Lite fallback" wording)
app_v4/tests/test_milvus_store.py      |  +++-  (mock rewrite + new tests)
docker-compose.yml                     |  +-    (updated status comment)
requirements-v2.txt                    |  +-    (version evidence comments)
```

### What was done (goals 1-6)

**Goal 1 — Version matrix** (official evidence):
- `langchain-milvus==0.4.0` forces `pymilvus>=3.0.0,<4.0` (PyPI requires_dist).
- `pymilvus==3.0.0` ↔ Milvus server 2.6.* (official compatibility matrix).
- Milvus server: `v2.6.21` (docker-compose.yml).
- Verified `langchain-milvus==0.3.3` + `pymilvus==2.6.17` is NOT viable: 0.3.3
  uses ORM `Collection(using=alias)` but pymilvus 2.6.x MilvusClient does not
  register ORM connections → `ConnectionNotExistException: should create
  connection first`.
- Locked: `langchain-milvus==0.4.0`, `pymilvus==3.0.0`.

**Goal 2 — Real first write** (two bugs found and fixed):
- langchain-milvus 0.4.0 `add_embeddings()` reuses kwargs dict for both
  `_init(**kwargs)` and `client.insert(timeout=, **kwargs)` → TypeError "got
  multiple values for keyword argument 'timeout'". Fix: do NOT pass `timeout=`
  to MilvusVectorStore constructor; pass it via `connection_args` instead.
- Milvus write goes to WAL buffer; must `client.flush(collection)` before
  stats/query see the data. Fix: `ingest()` calls `_flush()` after write.

**Goal 3 — Unit tests**:
- Rewrote `_FakeVectorStore` to match real official interface: added `client`
  attribute (`_FakeClient` with has_collection/get_collection_stats/query/flush),
  `add_texts()` method (real path, not upsert).
- `_existing_ids` changed from class variable to instance variable (no cross-test pollution).
- 9 unit tests pass.

**Goal 4 — Ingestion contract**:
- Stable chunk_id: `f"{doc_id}-c{j}"` where doc_id comes from
  `document_id > source > uuid4` (deterministic priority).
- `_validate_collection_schema()`: checks dense vector field + dimension,
  sparse vector field, BM25 built-in function (name starts with
  `bm25_function`), metadata fields (source/document_id/chunk_id).
  Raises `IncompatibleCollectionError` on mismatch (fail-fast).
- App-layer dedup: `_filter_existing()` queries collection for existing ids
  via `client.query`, only writes new ones (Milvus 2.6 does NOT dedupe on PK).
- `document_count()` no longer swallows all exceptions: returns 0 only when
  collection does not exist; raises on connection failure.

**Goal 5 — Real hybrid retrieval evidence**:
- Schema confirmed via `client.describe_collection()`:
  - `text` field: `enable_analyzer=true`, `analyzer_params={"type":"chinese"}`
  - `vector` (dense, dim=N) + `sparse` (type=104, BM25 output) fields
  - BM25 built-in function registered (name `bm25_function_*`, type=1)
- RRF reranker: `RRFRanker(k=60)` (BaseRanker, uses rank_params path).
  `Function(FunctionType.RERANK)` FAILS on Milvus 2.6 with "unsupported
  rerank function: []" (function_score path not supported).
- Chinese keyword "磁盘 df 命令" → top-1 = doc-01 (BM25 literal match,
  score 0.0328 vs 0.016 for others).

**Goal 6 — Config/doc cleanup**:
- `.env.example`: removed invalid `deepseek-embed` template.
- `settings.py`: removed "回退到嵌入式 Milvus Lite" wording.
- `docker-compose.yml`: updated "未安装 Docker" → current status.
- `requirements-v2.txt`: updated version evidence comments.

## Independent Audit Evidence (2026-07-28, independently revalidated)

```text
default offline suite (latest):    149 passed, 12 deselected, 0 failed
real Milvus integration:           5 passed
real embedding smoke:              2 passed (HTTP 400 fixed)
real Embedding + Milvus E2E:       1 passed (isolated collection, cleaned up)
real DeepSeek consult path:        PASS
real DeepSeek read-only ReAct:     PASS (HTTP 400 fixed, full 3-iteration loop)
message ID pairing unit test:      1 passed (new)
git diff --check:                  exit 0
```

### What was done (this round)

**Embedding adapter fix** (`app_v4/rag/real_embed.py`):
- Added `check_embedding_ctx_length=False` to the internal `OpenAIEmbeddings`
  constructor. Default `True` tokenizes text into token-ID arrays; Ollama's
  OpenAI-compatible endpoint expects raw strings and returns HTTP 400
  `invalid input type`. This is the official LangChain config knob — no
  hand-written HTTP, no new abstraction. Verified against langchain-openai 1.3.5.

**ReAct message protocol fix** (`app_v4/graph/readonly_react.py`):
- Added `AIMessage` to imports.
- Replaced isolated `ToolMessage` construction with standard message pairs:
  `AIMessage(content="", tool_calls=[{id, name, args}])` followed by
  `ToolMessage(tool_call_id=<same id>)`. Every ToolMessage now has a preceding
  matching `AIMessage.tool_calls` entry, which OpenAI/DeepSeek require.
- Tool output is still wrapped as untrusted data with injection warnings —
  protection not reduced.
- Added light prompt guidance toward safe in-scope arguments (e.g. `path="."`),
  so the real model is less likely to pick paths the safety validator rejects.

**New tests**:
- `app_v4/tests/test_real_embed_milvus_e2e.py` — real Ollama embedding →
  isolated temp Milvus collection (uuid-named) → ingest → hybrid retrieval →
  verify source/document_id/chunk_id/citation → BM25 top-1 = doc-01 →
  `finally` drops the collection and asserts it's gone. Marked `real_embedding`,
  skip if embedding/Milvus unconfigured. No Fake/SVD/mock.
- `test_react_message_protocol_tool_call_id_pairing` in `test_readonly_react.py`
  — intercepts the messages passed to the model and asserts every ToolMessage
  has a preceding AIMessage with a matching `tool_call_id`, plus correct
  name/args echoing.

**Test assertion realism** (`test_real_readonly_smoke.py`):
- The real model non-deterministically chooses arguments that the path-traversal
  safety validator correctly rejects (e.g. `path="/"`). That is safety working,
  not a pipeline failure. Changed the assertion from "every tool call must
  succeed" to "at least one tool call succeeds with real data, all calls have
  real structure, successful calls come from a real source". This is honest —
  it does not pretend a validation rejection is a pipeline bug.

## Known Gaps

1. MCP automated acceptance is now closed; remaining item is a real-model
   production smoke with `MCP_SERVER_URL` configured (the real `.env`
   currently has it empty, so production startup now intentionally fail-fast
   until the operator sets it).
2. SSE cancellation/backpressure and final user interview transfer remain
   pending.
3. Real-model readonly ReAct is still non-deterministic: the model may pick
   arguments the safety validator rejects on some runs. The pipeline handles
   this gracefully (error Observation fed back, loop continues), but a run is
   not guaranteed to show successful tool data every time. Prompt guidance
   mitigates but does not eliminate this.

## Uncompleted Acceptance Items

| Acceptance criterion | Status |
|---|---|
| pip check | ✅ pass |
| 聚焦 unit 全通过 | ✅ 9 passed |
| 真实 Milvus integration 全通过 | ✅ 5 passed |
| 真实 Embedding smoke 全通过 | ✅ 2 passed |
| 自动化真实 Embedding + Milvus E2E | ✅ 1 passed (collection cleaned up) |
| 真实 DeepSeek 普通咨询 | ✅ PASS |
| 真实 DeepSeek 只读 ReAct | ✅ PASS |
| 默认离线回归 | ✅ 149 passed, 12 deselected, 0 failed |
| 消息 ID 配对测试 | ✅ 1 passed (new) |
| 重复导入行数不增长 | ✅ verified (app-layer dedup) |
| schema/dimension 不兼容测试通过 | ✅ 2 unit tests (missing BM25, dim mismatch) |
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

Run after the embedding repair:

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests/test_real_embed_smoke.py -m real_embedding -vv -s -p no:cacheprovider --basetemp "$env:TEMP\appv4-real-embed"
```

Run after the ReAct message repair with real network access:

```powershell
.venv\Scripts\python -m pytest app_v4/tests/test_real_readonly_smoke.py -m real_chat -vv -s -p no:cacheprovider --basetemp "$env:TEMP\appv4-real-chat"
```

## Next Three Actions

1. Verify and repair real server-side SSE cancellation / backpressure.
2. Complete RAG evaluation/Badcase evidence.
3. Final interview transfer and user teach-back.

## Documentation Rules

- Current execution state: this file.
- Next-window recovery: `HANDOFF-LATEST.md`.
- Current acceptance overview: `FINAL-ACCEPTANCE-MATRIX.md`.
- Project-wide roadmap: `AGENT-CHAIN.md`.
- Market evidence: `INTERVIEW-MARKET.md`.
- Do not create additional status, audit, sprint, or acceptance Markdown files.
