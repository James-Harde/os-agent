"""MCP Client — 演示 tools/list 和 tools/call。

直接调用 MCPServer.handle() 验证协议。
生产环境应通过 HTTP/SSE/stdio 传输。
"""

from __future__ import annotations

import json
from typing import Any

from app_v4.mcp.server import MCPServer


class MCPClient:
    """本地 MCP Client（直接调用，不走网络）。"""

    def __init__(self) -> None:
        self._server = MCPServer()

    def list_tools(self) -> dict[str, Any]:
        """tools/list。"""
        return self._server.handle({
            "jsonrpc": "2.0", "id": "list-1", "method": "tools/list",
        })

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """tools/call。"""
        return self._server.handle({
            "jsonrpc": "2.0", "id": "call-1", "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })


if __name__ == "__main__":
    client = MCPClient()

    print("=" * 60)
    print("MCP tools/list")
    print("=" * 60)
    result = client.list_tools()
    tools = result.get("result", {}).get("tools", [])
    for t in tools:
        print(f"  {t['name']}: {t['description'][:50]}... [{t['permission']}]")
    print(f"\nTotal: {len(tools)} tools")

    print()
    print("=" * 60)
    print("MCP tools/call disk_usage")
    print("=" * 60)
    result = client.call_tool("disk_usage", {"path": "."})
    print(json.dumps(result, indent=2, ensure_ascii=False))
