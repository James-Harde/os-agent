# app_v4 Acceptance Matrix

Updated: 2026-07-28 (MCP repair verified)

Status meanings:

- `ENGINEERING PASS`: implementation, automated regression, and required
  real-environment evidence pass; learning transfer may still remain.
- `MOSTLY COMPLETE`: implementation and local regression evidence exist, but
  final real-environment or user teach-back evidence may remain.
- `PARTIAL`: a useful path exists but a required production-shaped behavior is
  missing.
- `NOT ACCEPTED`: the current implementation is not the approved main path.

| Area | Status | Existing evidence | Required before completion |
|---|---|---|---|
| Agent main path | MOSTLY COMPLETE | FastAPI entry, LangGraph routing, real DeepSeek read-only smoke, real tools, bounded ReAct, thread isolation, Trace, focused and black-box tests | user walkthrough and transfer assessment |
| Safety preflight | MOSTLY COMPLETE | dangerous execution denial, analysis-context distinction, injection scanning, zero-tool-call rejection tests | final scenario demonstration |
| Tool policy and HITL | MOSTLY COMPLETE | auto/confirm/deny, LangGraph interrupt/resume, approval API, audit, duplicate-resume tests | real approved adapter demonstration without unsafe host changes |
| Official MCP | ENGINEERING PASS | official FastMCP + Streamable HTTP; standalone CLI starts without MCP Client config; external list/call; `/api/chat`→MCP→real disk E2E; real DeepSeek→MCP→real tool smoke; production Web Agent fail-fast when MCP_SERVER_URL is empty; structured annotations/meta and error semantics; structured disconnect without local fallback; unified execution/audit; unique invocation IDs; mutation tools not exposed | Stage 4 user teach-back |
| RAG | PARTIAL | real Ollama Embedding, Docker Milvus Standalone, dense + built-in BM25 + RRF, citations, idempotent ingest, real E2E | versioned retrieval evaluation, Recall@k plus MRR/nDCG, and one measured reproducible Badcase |
| SSE and TTFT | PARTIAL | SSE endpoint, model stream events, TTFT fields, regression tests | prove disconnect cancels underlying work and document backpressure behavior |
| Rate limit/cache/budget | PARTIAL | token bucket, tool cache, step/tool/time budgets, repeat/no-progress guards, kill switch tests | measured behavior and clear production boundaries |
| Memory/context | PARTIAL | SQLite checkpoint, thread/user isolation, TTL and compression foundations | memory pollution/correction scenario and user explanation |
| Configuration/security | MOSTLY COMPLETE | centralized Settings, fixed `.env` location, secret fields hidden, chat/embedding config separated, dependency injection | clean-environment installation and startup smoke |
| Deployment/docs | PARTIAL | local startup and tests available | Docker Compose for app + Milvus, readiness, final README, reproducible demo |
| Interview transfer | PARTIAL | code and test evidence exists | evidence-backed interview notes and independent user teach-back |

## Current Blocking Order

1. SSE cancellation/backpressure.
2. RAG evaluation and measured Badcase.
3. Docker Compose application/readiness and final smoke tests.
4. Interview notes and user transfer assessment.

## Test Baseline

Latest independently verified (2026-07-28, MCP repair):

```text
default offline: 156 passed, 13 deselected
focused MCP: 18 passed
real MCP E2E: 3 passed
real DeepSeek -> MCP -> real tool: 1 passed
standalone MCP CLI startup: pass
pip check: pass
git diff --check: exit 0
```

Mock-only or baseline-only tests cannot promote a row to complete.
