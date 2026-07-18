# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Kylin Secure OS Agent** — a safety-first intelligent operations agent for Kylin/Linux OS (中国软件杯 A2 赛题). B/S architecture: user inputs natural language → Agent understands intent → calls read-only system tools → returns diagnosis. Protected by dual-layer sandbox (application + OS).

**Target platform**: LoongArch + Kylin Advanced Server V11
**Deadline**: End of July 2026

## Build & Run Commands

```powershell
# Install dependencies
pip install -r requirements.txt

# Start server (from kylin-os-agent/ directory)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Open browser: http://127.0.0.1:8000

# MCP smoke test (separate terminal, server must be running)
python scripts/mcp_smoke_test.py
```

**LLM Configuration**: Copy `.env.example` to `.env` and set `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_MODEL`. The adapter calls `{base_url}/chat/completions` via urllib (no OpenAI SDK dependency).

## Architecture

### Request Flow (`app/agent/orchestrator.py`)

The `AgentOrchestrator.handle()` method is the central pipeline:

1. **Memory load** — ensure conversation exists, load recent messages from SQLite
2. **Safety preflight** — `SafetyGuard.preflight_request()` blocks dangerous input before LLM
3. **LLM plan** — `ModelAdapter.plan()` sends system prompt + available tools to LLM, gets back `{intent, plan:[{tool, arguments, reason}]}`
4. **Safety assess** — `SafetyGuard.assess_request()` + `assess_plan()` validate the plan
5. **Tool execution** — loop over plan steps:
   - `execution_mode=auto` → `ToolRegistry.call()` (only read-only tools pass)
   - `execution_mode=confirm/deny` → create `ApprovalRequest` (blocked, not executed)
6. **Output scan** — `SafetyGuard.scan_untrusted_output()` on each tool result
7. **LLM summarize** — `ModelAdapter.summarize()` generates final Chinese answer
8. **Audit log** — everything recorded to SQLite

### Module Responsibilities

| Module | Role |
|--------|------|
| `app/main.py` | FastAPI app, 8 HTTP endpoints + 1 MCP endpoint + 3 approval endpoints, rate limiting |
| `app/agent/orchestrator.py` | Pipeline orchestration (preflight → plan → execute → summarize → audit) |
| `app/model/adapter.py` | OpenAI-compatible LLM client (urllib, no SDK). Handles plan/summarize/explain_denial |
| `app/safety/guard.py` | Multi-stage safety: preflight, assess_request, assess_plan, scan_untrusted_output, merge |
| `app/tools/registry.py` | Tool registration + sandbox gate (only `auto+read+read_only` tools execute) |
| `app/tools/system_tools.py` | 7 auto tools (disk/directory/port/process/log/service/injection) + 2 blocked tools |
| `app/tools/command_runner.py` | Whitelist-based command executor (shell=False, blocked tokens, shutil.which) |
| `app/tools/types.py` | `ToolSpec` dataclass — defines tool metadata (risk, permission, execution_mode) |
| `app/mcp/server.py` | MCP JSON-RPC server (initialize/tools/list/tools/call). Reuses ToolRegistry.call() |
| `app/mcp/schemas.py` | Pydantic models for JSON-RPC envelope + inputSchema for 7 auto tools |
| `app/approval/service.py` | SQLite-backed approval lifecycle (pending → approved/rejected) |
| `app/audit/logger.py` | SQLite audit tables (tool_calls + audit_logs) |
| `app/memory/store.py` | SQLite conversation memory (conversations + messages tables) |
| `app/config.py` | Path constants, loads `.env` into os.environ |
| `app/static/` | Frontend (Tailwind + DaisyUI dark theme) |

### Key Design Decisions

1. **LLM output is never directly executed** — `ToolRegistry.call()` hard-codes the gate: only tools with `execution_mode=auto AND read_only=True AND permission=read` run. The LLM can name any tool; non-auto ones are blocked.

2. **MCP reuses the same execution path** — `MCPServer._tools_call()` funnels through `ToolRegistry.call()`, not a separate path. `tools/list` only exposes auto-mode tools.

3. **SafetyGuard uses Unicode normalization** — `_normalize()` applies NFKC + strips zero-width chars before regex matching, preventing homoglyph/zero-width bypass.

4. **CommandRunner is defense-in-depth** — even though only auto tools call it, it independently enforces: whitelist (9 commands), blocked tokens (25 dangerous words), `shell=False`, `shutil.which` resolution.

5. **All persistence is SQLite** — single `data/audit.db` file with 5 tables. No external database dependency.

### HTTP API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/health | Health check |
| GET | /api/runtime | Model + sandbox status |
| GET | /api/tools | List all tool definitions |
| POST | /api/chat | Submit ops request (rate-limited 10/min) |
| GET | /api/audit | Query audit logs |
| GET | /api/conversations | List conversations |
| GET | /api/conversations/{id}/messages | Message history |
| POST | /api/mcp | JSON-RPC endpoint |
| GET | /api/approvals | List approval history |
| POST | /api/approvals/{id}/approve | Approve |
| POST | /api/approvals/{id}/reject | Reject |

### Tool Registry (9 tools)

**Auto (read-only, execute immediately)**: `disk_usage`, `directory_usage`, `port_lookup`, `process_list`, `system_logs`, `service_status`, `prompt_injection_scan`

**Confirm (require approval)**: `service_restart`
**Deny (always blocked)**: `file_delete`

## Project Conventions

- **Language**: Code comments and docstrings are in Chinese. LLM prompts are in Chinese.
- **No pip install by Agent** — only modify `requirements.txt`, user runs pip manually.
- **Safety changes** must be recorded in `docs/CHANGELOG-safety.md`.
- **Learning logs** go in `docs/learning/L{xx}-*.md` with 5-section format (Concept / In Our Code / Why It Matters / Common Pitfalls / Further Reading).
- **Confirmation gates**: <30 line edits OK to do directly; >30 line edits show diff first; new files show content first.

## Current Status

- Phase 1 (safety hardening) ✅
- Phase 2 (MCP server) ✅
- Phase 3 (approval flow) ✅
- UI rewrite ✅
- **Remaining**: Phase 4 (automated tests + test report), Phase 5 (demo materials/slides)
