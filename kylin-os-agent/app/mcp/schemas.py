"""JSON-RPC 2.0 + MCP-specific schemas and tool input schemas.

Reference: https://spec.modelcontextprotocol.io/
"""

from typing import Any

from pydantic import BaseModel, Field


class JSONRPCRequest(BaseModel):
    """JSON-RPC 2.0 request envelope (MCP transport-level wrapper)."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: Any = None
    error: JSONRPCError | None = None


class MCPToolInfo(BaseModel):
    """Shape of a single entry returned by MCP ``tools/list``."""

    name: str
    description: str
    inputSchema: dict[str, Any]


class MCPToolResult(BaseModel):
    """Shape of the ``content`` returned by MCP ``tools/call``."""

    content: list[dict[str, Any]] = Field(default_factory=list)
    isError: bool = False


# ---------------------------------------------------------------------------
# inputSchema for every auto-mode tool.
# These are read by MCP clients to understand which parameters are accepted.
# ---------------------------------------------------------------------------

TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "disk_usage": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to inspect. Constrained to PROJECT_ROOT at runtime.",
            }
        },
        "additionalProperties": False,
    },
    "directory_usage": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Root directory to scan. Constrained to PROJECT_ROOT at runtime.",
            }
        },
        "additionalProperties": False,
    },
    "port_lookup": {
        "type": "object",
        "properties": {
            "port": {
                "type": "integer",
                "minimum": 1,
                "maximum": 65535,
                "description": "TCP/UDP port number to look up.",
            }
        },
        "required": ["port"],
        "additionalProperties": False,
    },
    "process_list": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
                "description": "Maximum number of process rows to return.",
            }
        },
        "additionalProperties": False,
    },
    "system_logs": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 50,
                "description": "Maximum number of log lines to return.",
            }
        },
        "additionalProperties": False,
    },
    "service_status": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "maxLength": 80,
                "description": "Systemd unit name, e.g. 'sshd' or 'nginx.service'.",
            }
        },
        "required": ["service"],
        "additionalProperties": False,
    },
    "prompt_injection_scan": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Untrusted text to scan for prompt-injection patterns.",
            }
        },
        "required": ["content"],
        "additionalProperties": False,
    },
}
