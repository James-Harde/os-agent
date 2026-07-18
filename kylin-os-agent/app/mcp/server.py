"""MCP server — JSON-RPC 2.0 router for initialize / tools/list / tools/call.

Design note (important):
    ``tools/call`` does NOT open a second execution path. It funnels every
    invocation through ``ToolRegistry.call()``
    so all existing sandbox and safety rules still apply unchanged.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.mcp.schemas import JSONRPCRequest, TOOL_INPUT_SCHEMAS
from app.safety.guard import SafetyGuard
from app.audit.logger import AuditLogger
from app.tools.registry import ToolRegistry


class MCPServer:
    """MCP-over-JSON-RPC request handler.

    Parameters
    ----------
    tool_registry : ToolRegistry
        The shared registry that owns tool definitions and sandbox logic.
    safety_guard : SafetyGuard
        Safety guard instance. MCP calls are still pre-flighted before dispatch.
    audit_logger : AuditLogger
        Every MCP tool call is recorded identical to non-MCP calls.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        safety_guard: SafetyGuard,
        audit_logger: AuditLogger,
    ) -> None:
        self.tool_registry = tool_registry
        self.safety_guard = safety_guard
        self.audit_logger = audit_logger
        self._auto_tool_names = {
            spec["name"]
            for spec in tool_registry.list_specs()
            if spec["execution_mode"] == "auto"
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def handle_request(self, req: JSONRPCRequest) -> dict[str, Any]:
        """Route a JSON-RPC request and return a result dict (no error key)."""
        routes = {
            "initialize": self._initialize,
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
        }
        handler = routes.get(req.method)
        if handler is None:
            return self._error(-32601, f"method not found: {req.method}")
        try:
            return handler(req)
        except Exception as exc:  # MCP spec: internal error
            return self._error(-32603, str(exc))

    # ------------------------------------------------------------------
    # MCP methods
    # ------------------------------------------------------------------

    def _initialize(self, req: JSONRPCRequest) -> dict[str, Any]:
        """Return server info and capabilities per MCP spec."""
        _ = req  # unused — initialize takes no params
        return {
            "protocolVersion": "2025-03-26",
            "serverInfo": {
                "name": "kylin-secure-os-agent",
                "version": "0.2.0",
            },
            "capabilities": {
                "tools": {"listChanged": False},
            },
        }

    def _tools_list(self, req: JSONRPCRequest) -> dict[str, Any]:
        """Return only auto-mode read-only tools."""
        _ = req
        tools: list[dict[str, Any]] = []
        for spec in self.tool_registry.list_specs():
            name = spec["name"]
            if name not in self._auto_tool_names:
                continue  # hide confirm/deny tools from external MCP callers
            schema = TOOL_INPUT_SCHEMAS.get(name, {"type": "object", "properties": {}})
            tools.append({
                "name": name,
                "description": spec["description"],
                "inputSchema": schema,
                "x-permission": spec["permission"],
                "x-execution_mode": spec["execution_mode"],
                "x-sandbox_scope": spec["sandbox_scope"],
            })
        return {"tools": tools}

    def _tools_call(self, req: JSONRPCRequest) -> dict[str, Any]:
        """Invoke a tool through ToolRegistry (sandbox + SafetyGuard apply)."""
        params = req.params or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}

        if not isinstance(name, str) or name not in self._auto_tool_names:
            content_text = json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)
            return {
                "content": [{"type": "text", "text": content_text}],
                "isError": True,
            }

        # Pre-flight: apply SafetyGuard before dispatching.
        preflight = self.safety_guard.preflight_request(f"mcp tools/call {name}")
        if preflight["decision"] == "deny":
            content_text = json.dumps({
                "error": "safety guard denied",
                "reasons": preflight["reasons"],
            }, ensure_ascii=False)
            return {
                "content": [{"type": "text", "text": content_text}],
                "isError": True,
            }

        # ⭐ Reuse ToolRegistry.call() — same sandbox + audit path as /api/chat.
        call_result = self.tool_registry.call(
            name=name,
            arguments=arguments,
            request_id=f"mcp-{uuid.uuid4()}",
            reason="mcp_tools_call",
        )
        is_error = call_result["status"] in ("blocked", "error")
        content_text = json.dumps(call_result, ensure_ascii=False)
        return {
            "content": [{"type": "text", "text": content_text}],
            "isError": is_error,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _error(code: int, message: str) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": message}, ensure_ascii=False)}],
            "isError": True,
        }
