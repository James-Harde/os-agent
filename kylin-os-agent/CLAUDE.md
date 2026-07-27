# Claude Code Project Rules

## Project Goal

`app_v4` is the only active implementation. It is a job-search portfolio for
Agent, LLM application, and backend roles.

The project may stay small, but every active capability must use a mainstream,
maintainable engineering approach. Do not use competition constraints,
zero-dependency goals, domestic-only substitutions, or hand-written teaching
implementations as production decisions.

Do not modify `app`, `app_v2`, or `app_v3` unless the user explicitly changes
the scope. Never read, print, overwrite, or commit secrets from `.env`.

## Recovery Path

For every continuing window, read only:

1. `D:\klin-agent\app4-需求清单.md`
2. `app_v4/docs/WORK-STATE.md`
3. `app_v4/docs/HANDOFF-LATEST.md`
4. `git status --short` and the focused Git diff

Then continue from the handoff's next action. Do not restart a full-repository
audit. `AGENT-CHAIN.md` is the project roadmap, not the per-window execution
prompt. `INTERVIEW-MARKET.md` is market evidence and is read only when the
roadmap is recalibrated.

## Current Architecture

- FastAPI is the HTTP boundary.
- LangGraph `StateGraph` owns state, routing, checkpointing, loops, and HITL.
- LangChain owns model, message, tool, splitter, embedding, retriever, and
  vector-store integrations.
- The outer workflow is deterministic and safety-controlled.
- Read-only diagnosis uses bounded ReAct.
- Mutating actions use plan, policy validation, HITL, frozen parameters,
  execution, and verification.
- The official MCP SDK is the protocol layer. MCP and `/api/chat` must reuse
  the same tool policy and audit path.

Model output is an untrusted structured proposal. The orchestration layer must
validate schema, tool allowlist, arguments, permission, risk, budget, and
approval before execution.

## Mainstream Component Gate

Before implementing a common capability:

1. Name the problem and whether the project actually needs it.
2. Identify the mainstream framework or managed component normally used.
3. Reuse that component unless a concrete constraint prevents it.
4. If a fallback is necessary, isolate and label it; never make it the
   production default or PASS evidence.
5. Remove the superseded custom implementation and obsolete tests after the
   replacement passes.

Do not repair or extend the old hand-written RAG adapters. The approved target
is:

- LangChain `Document` and `RecursiveCharacterTextSplitter`
- a real embedding model configured separately from the chat model
- Milvus Standalone through Docker Compose
- Milvus dense retrieval plus built-in BM25 sparse retrieval and RRF fusion
- source citations and a small versioned retrieval evaluation set

Cross-encoder reranking, query rewriting, parent-child indexing, and other
optimizations are added only when a measured bad case justifies them.

## Commands

Run from `D:\klin-agent\kylin-os-agent`:

```powershell
pip install -r requirements-v2.txt
python -m uvicorn app_v4.main:app --host 127.0.0.1 --port 8000
python -m pytest app_v4/tests -q -p no:cacheprovider
```

Use `APP_V4_USE_FAKE_MODEL=true` only for deterministic automated tests.
Production smoke tests must use explicit real model and embedding
configuration without exposing credentials.

## Dependency Installation

- Project-local Python dependencies may be installed into the project `.venv`
  after checking official compatibility and locking the verified versions.
- System-level software such as Docker Desktop, WSL, database services, or
  virtualization features may also be installed when the project needs them,
  but not silently.
- Before a system-level installation, report the official source and version,
  exact commands, disk/service/virtualization/restart impact, and rollback
  approach. Wait for explicit user approval, then perform and verify the
  installation.
- Missing infrastructure is a request-for-approval point, not permission to
  replace the approved component with an unsupported fallback.

## Change And Evidence Rules

- Keep one production default path.
- Use dependency injection for external services and test doubles.
- Do not claim PASS from file existence, mock-only tests, or helper counts.
- Record real commands, test results, Trace IDs, metrics, and limitations.
- Keep `WORK-STATE.md` concise and current.
- Keep `HANDOFF-LATEST.md` as a short recovery capsule with at most three next
  actions.
- When context grows large, stop expanding scope, run the smallest meaningful
  tests, update both state files, and hand off.
- Delete stale status documents rather than preserving contradictory copies.
