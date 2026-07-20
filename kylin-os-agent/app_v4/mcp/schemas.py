"""MCP 协议 schema — 基于 Model Context Protocol 规范。

参考：https://modelcontextprotocol.io/specification
核心方法：tools/list, tools/call
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


# ---------------------------------------------------------------------------
# JSON-RPC 信封
# ---------------------------------------------------------------------------
class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = {}


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: Any = None
    error: JSONRPCError | None = None


# ---------------------------------------------------------------------------
# MCP tools/list 响应
# ---------------------------------------------------------------------------
class MCPToolInfo(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any] = {}
    riskLevel: str = "low"        # low / medium / high
    permission: str = "auto"      # auto / confirm / deny


class MCPListToolsResult(BaseModel):
    tools: list[MCPToolInfo]


# ---------------------------------------------------------------------------
# MCP tools/call 响应
# ---------------------------------------------------------------------------
class MCPCallToolResult(BaseModel):
    content: list[dict[str, Any]] = []
    isError: bool = False
