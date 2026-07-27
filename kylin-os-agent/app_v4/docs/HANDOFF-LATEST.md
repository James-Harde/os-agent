# app_v4 Latest Handoff

Updated: 2026-07-26

## Resume

Read:

1. `D:\klin-agent\app4-需求清单.md`
2. `app_v4/docs/WORK-STATE.md`
3. this file
4. focused RAG Git diff and `git status --short`

Do not repeat a full-repository audit.

## This Window Summary

RAG foundation repaired from NOT ACCEPTED to **real-component, test-covered,
Docker-blocked**. Replaced the PyMilvus hand-written Milvus integration with the
official langchain-milvus `Milvus` vector store + `BM25BuiltInFunction` + RRF
`Function(FunctionType.RERANK)`.

Verified results (project `.venv`, Python 3.13.9):

```text
test_milvus_store.py unit:          7 passed (mock MilvusVectorStore)
test_rag_milvus_tool.py:            4 passed (tool boundary, DI)
test_milvus_store.py integration:   4 skipped (no Docker — Milvus unreachable)
test_agent.py + test_safety.py:     33 passed (no regression)
rag_search w/o MILVUS_URI:          structured unavailable (no Lite fallback)
```

Official version evidence (PyPI requires_dist + pymilvus compatibility matrix):
- `langchain-milvus==0.4.0` forces `pymilvus>=3.0.0,<4.0`
- `pymilvus==3.0.0` ↔ Milvus server 2.6.*; RRF `Function` requires Milvus 2.6+
- Milvus server: `v2.6.21` (docker-compose.yml, latest stable 2026-07-24)
- milvus-lite removed (Windows unsupported: `sys_platform != "win32"`)

Files changed this window:
- `app_v4/rag/milvus_store.py` (rewritten: official `Milvus` + BM25 + RRF)
- `app_v4/rag/store_factory.py` (fail-fast on missing MILVUS_URI; no Lite)
- `app_v4/rag/real_embed.py` (now `Embeddings` subclass for official API)
- `app_v4/rag/dense_embed.py` (now `Embeddings` subclass for test injection)
- `app_v4/container.py` (added `rag_store` DI property + setter)
- `app_v4/tools/system_tools.py` (removed module-level `_rag_store` singleton)
- `app_v4/tests/test_milvus_store.py`, `test_rag_milvus_tool.py` (rewritten)
- `requirements-v2.txt` (removed milvus-lite; locked versions)
- `docker-compose.yml` (Milvus v2.5.11 → v2.6.21)
- `.env.example` (Milvus fail-fast doc)

## Blocking Finding

**Docker Desktop not installed** — Milvus Standalone cannot run. This blocks:
real dense + BM25 + RRF end-to-end, real Embedding write/query, Chinese BM25
discriminative test (top-1), and idempotent-ingestion proof on real Milvus.

Installation review submitted in WORK-STATE.md. Waiting for explicit user
approval before installing Docker Desktop.

## Next Three Actions

1. Get user approval for Docker Desktop; run `docker compose up -d`; execute
   `pytest app_v4/tests/test_milvus_store.py -m integration` (real Milvus).
2. With Docker running, run real Embedding smoke (report missing non-secret
   fields only if absent; never fabricate/print keys).
3. After real Milvus + Embedding evidence, mark RAG foundation PASS; then resume
   MCP / SSE work.

## First Command (next window)

```powershell
cd D:\klin-agent\kylin-os-agent; .venv\Scripts\pytest app_v4\tests\test_milvus_store.py -q -p no:cacheprovider
```
