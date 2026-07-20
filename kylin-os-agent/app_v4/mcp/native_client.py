"""标准 MCP Client — 通过 stdio transport 调用本地 MCP Server。

修复 audit #11：Web Agent 经 MCP Client/transport 调用本地 MCP Server，
不能直接实例化 server 或绕过协议。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

# 使用 streamable_http transport（比 stdio 更适合 Web 场景）
# 如需 stdio，可替换为 mcp.client.stdio.stdio_client


class NativeMCPClient:
    """通过 streamable_http transport 连接 MCP Server。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8001") -> None:
        self._base_url = base_url

    async def list_tools(self) -> list[dict[str, Any]]:
        """标准 MCP tools/list。"""
        async with streamablehttp_client(self._base_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()  # 标准 initialize 生命周期
                tools = await session.list_tools()
                return [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
                    }
                    for t in tools.tools
                ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """标准 MCP tools/call。"""
        async with streamablehttp_client(self._base_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                text = ""
                for content in result.content:
                    if hasattr(content, "text"):
                        text += content.text
                return json.loads(text) if text else {}


def run_async(coro):
    """同步入口，方便测试调用。"""
    return asyncio.run(coro)


if __name__ == "__main__":
    client = NativeMCPClient()
    tools = run_async(client.list_tools())
    print(f"MCP tools/list: {len(tools)} tools")
    for t in tools:
        print(f"  {t['name']}: {t['description'][:50]}...")
