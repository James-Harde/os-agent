# app_v4 Current Work State

Updated: 2026-07-26

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
| Agent main path | Mostly complete | `/api/chat`, real read-only tools, thread isolation, Trace, deterministic outer workflow, and bounded read-only ReAct exist |
| Safety and HITL | Mostly complete | dangerous-request denial, injection handling, auto/confirm/deny policy, `interrupt()` and `Command(resume=...)`, audit, and idempotent approval behavior exist |
| MCP | Partial | official FastMCP server/client and streamable HTTP tests exist; production `/api/chat` still needs one clear default MCP path and duplicate legacy surfaces need consolidation |
| RAG | Foundation repaired (pending real Milvus + Embedding evidence) | official langchain-milvus `Milvus` + `BM25BuiltInFunction` + RRF `Function` path implemented; Lite fallback removed; DI wired; unit tests pass; integration/smoke blocked on Docker |
| Streaming/performance | Partial | SSE, TTFT fields, token bucket, cache, budgets, and kill switch exist; real server-side cancellation/backpressure still needs verification and repair |
| Memory/context | Basic complete | thread checkpoints, SQLite long-term memory, user/thread isolation, TTL, and compression foundations exist; no need to add a memory platform without a measured use case |
| Delivery evidence | Partial | automated tests are broad, but README, Docker Compose, real-service smoke evidence, and interview notes are not complete |

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

## Evidence

RAG foundation repair (2026-07-26, this window):

```text
Version decision (official PyPI metadata):
  - langchain-milvus==0.4.0 → requires pymilvus>=3.0.0,<4.0
  - pymilvus==3.0.0 → Milvus server 2.6.* compatible; RRF Function requires Milvus 2.6+
  - Milvus server: v2.6.21 (docker-compose.yml, latest stable 2026-07-24)
  - milvus-lite removed: Windows unsupported (sys_platform != "win32" constraint)
Project .venv test suite:
  test_milvus_store.py: 7 passed (unit, mock MilvusVectorStore)
  test_rag_milvus_tool.py: 4 passed (tool boundary, DI)
  test_milvus_store.py integration: 4 skipped (no Docker)
  test_agent.py + test_safety.py: 33 passed (no regression)
Production behavior:
  rag_search without MILVUS_URI → structured unavailable (no Lite fallback)
```

What remains unverified without Docker:
  - Real Milvus Standalone end-to-end (dense + BM25 + RRF)
  - Real Embedding write + semantic query
  - Chinese BM25 analyzer discriminative power (top-1 assertion)
  - Idempotent ingestion on real Milvus (no chunk doubling)

## Known Gaps

1. **Docker Desktop not installed** — Milvus Standalone cannot run; integration
   tests skip. BLOCKING real-service evidence (see installation review below).
2. Real Embedding configuration (EMBEDDING_BASE_URL / API_KEY / MODEL) not
   validated end-to-end — smoke test requires credentials + Docker.
3. `/api/chat` MCP consolidation, SSE cancellation/backpressure, and final user
   interview transfer remain pending.

## Installation Review: Docker Desktop

Required to run Milvus Standalone (RAG production dependency).
- **Why required**: Milvus Standalone needs Docker; no Windows-native
  alternative is approved (milvus-lite is Windows-unsupported).
- **Source**: https://www.docker.com/products/docker-desktop/ (Docker Desktop,
  latest stable). Windows requires WSL 2 backend.
- **Exact commands** (after download):
  `Docker Desktop Installer.exe install`
  Then: `docker compose up -d` (from project root) starts etcd + minio + milvus.
- **Disk/service/virtualization impact**: ~2-4 GB disk; WSL 2 enabled (no
  restart if WSL already on; otherwise one restart); background Docker service.
- **Rollback**: `docker compose down -v` removes containers + volumes; uninstall
  Docker Desktop from Apps & Features.
- **Verification**: `docker compose ps` + `curl http://localhost:9091/healthz`.
- **Status**: PENDING USER APPROVAL — do not install until explicitly approved.

## Next Three Actions

1. Obtain user approval to install Docker Desktop; run Milvus Standalone and
   execute integration tests (real dense + BM25 + RRF + Chinese top-1 assertion).
2. With Docker running, validate real Embedding end-to-end (report missing
   non-secret fields only if credentials absent; never fabricate keys).
3. After RAG passes with real evidence, wire Recall@k/MRR/nDCG + Badcase; then
   resume MCP and SSE work.

## Documentation Rules

- Current execution state: this file.
- Next-window recovery: `HANDOFF-LATEST.md`.
- Current acceptance overview: `FINAL-ACCEPTANCE-MATRIX.md`.
- Project-wide roadmap: `AGENT-CHAIN.md`.
- Market evidence: `INTERVIEW-MARKET.md`.
- Do not create additional status, audit, sprint, or acceptance Markdown files.
