# app_v4 Latest Handoff

Updated: 2026-07-28 (independently revalidated)

## Resume

Read:

1. `D:\klin-agent\app4-需求清单.md`
2. `app_v4/docs/WORK-STATE.md`
3. this file
4. `git status --short` and the focused diff

Do not repeat a full-repository audit.

## This Window Summary

The two real-chain blockers are **fixed and verified** against real providers.
Codex independently reran every gate below on 2026-07-28; the reported results
match the implementation.

Verified results (project `.venv`, Python 3.13.9):

```text
default offline suite:             153 passed, 10 deselected, 0 failed
real Milvus integration:           5 passed
real embedding smoke:              2 passed
real Embedding + Milvus E2E:       1 passed (isolated collection, cleaned up)
real DeepSeek consult:             PASS
real DeepSeek read-only ReAct:     PASS (full 3-iteration loop)
message ID pairing unit test:      1 passed (new)
git diff --check:                  exit 0
```

## Key Technical Decisions (official evidence)

- `langchain-milvus==0.4.0` + `pymilvus==3.0.0` ↔ Milvus server 2.6.*
  (PyPI requires_dist + official compatibility matrix).
- **0.3.3 + 2.6.x NOT viable**: verified empirically — 0.3.3 uses ORM
  `Collection(using=alias)` but pymilvus 2.6.x MilvusClient does not register
  ORM connections → `ConnectionNotExistException`.
- **RRF reranker**: `RRFRanker(k=60)` (BaseRanker, rank_params path). NOT
  `Function(FunctionType.RERANK)` — fails on Milvus 2.6 with "unsupported
  rerank function: []" (function_score path unsupported).
- **langchain-milvus 0.4.0 timeout bug**: do NOT pass `timeout=` to the
  MilvusVectorStore constructor (`add_embeddings` reuses kwargs for both
  `_init(**kwargs)` and `client.insert(timeout=, **kwargs)` → TypeError).
  Pass timeout via `connection_args` to MilvusClient instead.
- **Milvus flush required**: after insert/upsert, data sits in WAL buffer;
  must call `client.flush(collection)` before stats/query see it.
- **App-layer dedup**: Milvus 2.6 insert/upsert do NOT dedupe on primary key;
  ingest queries existing ids via `client.query` and only writes new ones.

## Real Milvus Evidence

- Schema: text field `enable_analyzer=true` + `analyzer_params={"type":"chinese"}`,
  `vector` (dense, dim=N) + `sparse` (type=104) fields, BM25 built-in function
  registered (name `bm25_function_*`).
- Chinese "磁盘 df 命令" → top-1 = doc-01 (BM25 literal match, score 0.0328 vs 0.016).
- Idempotent ingestion verified (row_count stable across repeated imports).
- Schema validation: `_validate_collection_schema()` checks dense+sparse fields,
  dimension, BM25 function, metadata fields; raises `IncompatibleCollectionError`.

## Files Changed This Round (8 files, +523/-163)

- `app_v4/rag/milvus_store.py` — core rewrite: official Milvus + BM25 + RRFRanker;
  flush; app-layer dedup; schema validation; `IncompatibleCollectionError`; `_RRFReranker`
- `app_v4/tests/test_milvus_store.py` — rewritten `_FakeVectorStore` (client attr,
  instance state, add_texts); schema validation tests; real Milvus schema evidence test
- `.env.example` — removed invalid deepseek-embed template
- `app_v4/settings.py` — removed "Lite fallback" wording
- `docker-compose.yml` — updated status comment
- `requirements-v2.txt` — version evidence comments
- `app_v4/docs/WORK-STATE.md`, `HANDOFF-LATEST.md` — state docs

## Blocking Findings — RESOLVED

1. ~~Embedding HTTP 400~~ — FIXED: added `check_embedding_ctx_length=False` to
   the internal `OpenAIEmbeddings` in `real_embed.py`. Verified: 2 smoke + 1
   E2E pass.
2. ~~ReAct HTTP 400~~ — FIXED: `readonly_react.py` now builds standard
   `AIMessage(tool_calls=[{id,name,args}])` → `ToolMessage(tool_call_id=id)`
   pairs. Verified: real DeepSeek runs a full 3-iteration loop; new unit test
   asserts ID pairing.
3. ~~Missing E2E~~ — FIXED: `test_real_embed_milvus_e2e.py` covers real
   embedding → isolated collection → write → hybrid retrieval → citation →
   cleanup (asserts collection dropped).

Residual test-lifecycle edge: the E2E cleanup flag is set only after
`ingest()` returns, so a partial ingest failure after collection creation could
leave an orphan collection. The verified successful path cleaned up and an
independent collection listing found zero `e2e_embed_*` leftovers. Repair this
edge at the start of the next implementation window.

## Acceptance Items (this round — all green)

| Criterion | Status |
|---|---|
| pip check | ✅ |
| 聚焦 unit 全通过 | ✅ 9 passed |
| 真实 Milvus integration 全通过 | ✅ 5 passed |
| 真实 Embedding smoke 全通过 | ✅ 2 passed |
| 自动化真实 Embedding + Milvus E2E | ✅ 1 passed (cleaned up) |
| 真实 DeepSeek 普通咨询 | ✅ PASS |
| 真实 DeepSeek 只读 ReAct | ✅ PASS |
| 默认离线回归 | ✅ 153 passed, 10 deselected, 0 failed |
| 消息 ID 配对测试 | ✅ 1 passed (new) |
| git diff --check 通过 | ✅ exit 0 |

## Files Changed This Round (two-blocker fix)

- `app_v4/rag/real_embed.py` — added `check_embedding_ctx_length=False`
- `app_v4/graph/readonly_react.py` — AIMessage.tool_calls → ToolMessage pairs;
  added AIMessage import; light prompt guidance for safe args
- `app_v4/tests/test_real_embed_milvus_e2e.py` — NEW: real Embedding + Milvus E2E
- `app_v4/tests/test_readonly_react.py` — NEW: message ID pairing test
- `app_v4/tests/test_real_readonly_smoke.py` — realistic success assertion
- `app_v4/docs/WORK-STATE.md`, `HANDOFF-LATEST.md` — state docs

## First Commands (regression guard)

```powershell
cd D:\klin-agent\kylin-os-agent
.venv\Scripts\python -m pytest app_v4/tests -q -p no:cacheprovider
```

## Next Three Actions

1. Consolidate `/api/chat` MCP path: one default production route, remove
   duplicate legacy surfaces, reuse existing tool policy + audit.
2. Verify / repair real server-side SSE cancellation and backpressure.
3. Final interview transfer: README, real-service smoke evidence, interview notes.
