# Kylin OS Agent Roadmap

Updated: 2026-07-26

## Purpose

This is the project-wide roadmap for the `app_v4` job-search portfolio.
Per-window execution state belongs in:

- `app_v4/docs/WORK-STATE.md`
- `app_v4/docs/HANDOFF-LATEST.md`

Market evidence and volatile interview frequency belong in
`INTERVIEW-MARKET.md`.

## Product Boundary

The product is a small, safety-controlled operations Agent:

- FastAPI API and UI
- LangGraph deterministic outer workflow
- bounded ReAct for read-only diagnosis
- fixed plan/policy/HITL/execute/verify path for mutations
- official MCP for remote tool protocol
- real RAG for operations knowledge
- Trace, audit, evaluation, testing, and Docker Compose

The project does not include model training, vLLM/Triton, Kubernetes-scale
deployment, multi-agent, Deep Agents, or Skills unless a later verified use
case requires a separate experiment.

## Engineering Rules

1. Prefer mature mainstream components for general capabilities.
2. Hand-written replacements may exist only as isolated test doubles or
   explicitly temporary learning baselines.
3. A feature passes only with a real code path plus tests and operational
   evidence.
4. Keep one production default path and delete superseded implementations.
5. Build the thinnest runnable vertical slice before deeper hardening.
6. Do not invent production scale, business metrics, or incident experience.

## Learning Gate

Each chain has four stages:

1. Understand boundaries, inputs, outputs, call order, and defects.
2. Implement and read the important code.
3. Run automated tests and real-environment checks.
4. Explain, diagnose, or modify the chain independently.

Code completion alone does not prove interview mastery.

## Roadmap

| Phase | Deliverable | Status | Next gate |
|---|---|---|---|
| 1. Agent main path | `/api/chat`, real read-only tools, thread isolation, Trace, bounded ReAct | Mostly complete | real-model smoke and user walkthrough |
| 2. Safety and HITL | dangerous denial, context distinction, injection defense, policy levels, interrupt/resume | Mostly complete | final scenario demo and teach-back |
| 3. MCP | official Server/Client, schemas, shared policy/audit | Partial | make one production Agent path use MCP by default |
| 4. RAG | real embedding, Milvus, hybrid retrieval, citations, evaluation | Not accepted | implement the thin Milvus vertical slice |
| 5. Performance | SSE, TTFT, cancellation, backpressure, rate limit, cache, budgets | Partial | prove underlying cancellation and measure behavior |
| 6. Memory/context | checkpoint, short/long-term boundaries, expiry, correction, compression | Basic complete | pollution/correction scenario and teach-back |
| 7. Delivery | Docker Compose, startup/readiness, final tests, README, interview notes | Partial | complete after phases 4 and 5 |

## Current Phase

Active phase: `4. RAG`

Approved first slice:

```text
LangChain Document
  -> RecursiveCharacterTextSplitter
  -> real Embedding
  -> Milvus Standalone
  -> dense retrieval + built-in BM25
  -> RRF
  -> citations
```

The first slice does not include cross-encoder reranking, query rewriting, or
parent-child indexing. Add them only for measured retrieval failures.

The old FAISS/rank-bm25/custom-adapter repair plan is cancelled.

## Acceptance Evidence By Phase

### Agent Main Path

- real `disk_usage`, `process_list`, and `port_lookup` results;
- same-thread follow-up and concurrent thread isolation;
- Trace with node transitions, decisions, tool calls, duration, and status;
- normal consultation, tool failure, and bounded-loop tests.

### Safety And HITL

- `rm -rf /` denied with zero tool calls and an audit record;
- dangerous text in an analysis request treated as untrusted data;
- auto/confirm/deny permission behavior;
- approve/reject and idempotent resume from the same checkpoint.

### MCP

- official SDK `tools/list` and `tools/call`;
- streamable HTTP client/server integration;
- schemas and risk metadata;
- Agent and external client reuse the same policy and audit service.

### RAG

- real document ingestion and chunk metadata;
- persistent Milvus collection/index;
- real embedding and dense retrieval;
- Milvus BM25 sparse retrieval and RRF;
- source citations;
- versioned corpus/query/qrels and Recall@k plus MRR or nDCG;
- at least one genuine, reproducible bad case.

### Performance

- actual SSE model stream;
- TTFT and total latency;
- disconnect stops underlying work;
- rate limit, cache, loop/tool/time budget, and kill-switch evidence.

### Memory And Context

- same-thread continuity and different-thread isolation;
- stable user identity for cross-thread memory;
- write/retrieve/expiry/correction policy;
- pollution and stale-memory scenario;
- selective context injection or compression evidence.

### Delivery

- clean-environment dependency installation;
- Docker Compose for app and Milvus;
- readiness and real-service smoke test;
- final automated and manual acceptance;
- evidence-backed interview notes and user teach-back.

## Interview Coverage

| Interview domain | Project evidence |
|---|---|
| Workflow vs Agent, ReAct, Plan-and-Execute | hybrid LangGraph topology and bounded loop |
| LangChain vs LangGraph | ecosystem integrations vs stateful orchestration |
| Tool calling and safety | candidate action validation, policy, audit, HITL |
| MCP | protocol/transport vs model tool-call format and local registry |
| RAG | chunking, embedding, Milvus, hybrid retrieval, RRF, citations, metrics |
| Context and memory | checkpoint, short/long-term separation, expiry, correction |
| Evaluation | black-box tests, Trace attribution, retrieval metrics, Badcases |
| Backend reliability | DI, SQLite boundaries, rate limit, cache, SSE, cancellation |

## Current Evidence

Last independently verified pre-RAG-migration regression baseline:

```text
191 passed, 4 deselected
```

This is not final product acceptance.

## Next Milestone

Complete the Milvus RAG vertical slice, remove the superseded custom RAG path,
run focused and full regression tests, and then update:

1. `app_v4/docs/WORK-STATE.md`
2. `app_v4/docs/HANDOFF-LATEST.md`
3. `app_v4/docs/FINAL-ACCEPTANCE-MATRIX.md`

Do not create another roadmap or status document.
