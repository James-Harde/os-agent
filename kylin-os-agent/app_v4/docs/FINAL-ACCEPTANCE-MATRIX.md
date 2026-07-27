# app_v4 Acceptance Matrix

Updated: 2026-07-26

Status meanings:

- `MOSTLY COMPLETE`: implementation and local regression evidence exist, but
  final real-environment or user teach-back evidence may remain.
- `PARTIAL`: a useful path exists but a required production-shaped behavior is
  missing.
- `NOT ACCEPTED`: the current implementation is not the approved main path.

| Area | Status | Existing evidence | Required before completion |
|---|---|---|---|
| Agent main path | MOSTLY COMPLETE | FastAPI entry, LangGraph routing, real read-only tools, bounded ReAct, thread isolation, Trace, focused and black-box tests | final real-model smoke and user walkthrough |
| Safety preflight | MOSTLY COMPLETE | dangerous execution denial, analysis-context distinction, injection scanning, zero-tool-call rejection tests | final scenario demonstration |
| Tool policy and HITL | MOSTLY COMPLETE | auto/confirm/deny, LangGraph interrupt/resume, approval API, audit, duplicate-resume tests | real approved adapter demonstration without unsafe host changes |
| Official MCP | PARTIAL | FastMCP server, official client session, streamable HTTP integration tests | make one production `/api/chat` path use MCP by default and remove duplicate legacy path |
| RAG | NOT ACCEPTED | custom PyMilvus path passes Fake-Embedding Lite tests only; Docker/real Embedding not run and `.venv` cannot import the new stack | official LangChain Milvus integration, compatible versions, Standalone fail-fast, Chinese analyzer, DI, idempotent ingestion, real smoke, citations and metrics |
| SSE and TTFT | PARTIAL | SSE endpoint, model stream events, TTFT fields, regression tests | prove disconnect cancels underlying work and document backpressure behavior |
| Rate limit/cache/budget | PARTIAL | token bucket, tool cache, step/tool/time budgets, repeat/no-progress guards, kill switch tests | measured behavior and clear production boundaries |
| Memory/context | PARTIAL | SQLite checkpoint, thread/user isolation, TTL and compression foundations | memory pollution/correction scenario and user explanation |
| Configuration/security | MOSTLY COMPLETE | centralized Settings, fixed `.env` location, secret fields hidden, chat/embedding config separated, dependency injection | clean-environment installation and startup smoke |
| Deployment/docs | PARTIAL | local startup and tests available | Docker Compose for app + Milvus, readiness, final README, reproducible demo |
| Interview transfer | PARTIAL | code and test evidence exists | evidence-backed interview notes and independent user teach-back |

## Current Blocking Order

1. Milvus RAG vertical slice.
2. MCP production-path consolidation.
3. SSE cancellation/backpressure.
4. Docker Compose and final smoke tests.
5. Interview notes and user transfer assessment.

## Test Baseline

Last independently verified before the RAG migration:

```text
191 passed, 4 deselected
```

Mock-only or baseline-only tests cannot promote a row to complete.
