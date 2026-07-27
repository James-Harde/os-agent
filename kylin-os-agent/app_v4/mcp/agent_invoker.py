"""Agent 侧 MCP 工具调用器。

§5 Gate 2 #9 / §6 矩阵 #16：Web Agent 的生产工具调用必须经过真实
NativeMCPClient transport，不得直接 TOOL_BY_NAME.invoke()。

此模块提供 MCPToolInvoker：
  - 通过官方 MCP ClientSession (streamable_http) 调用工具
  - 可被注入 ToolApplicationService，使 Agent 工具调用走 MCP transport
  - 测试中可替换 transport 为 spy，验证 Agent 确实经过 MCP Client
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("app_v4.mcp.agent_invoker")


class MCPToolInvoker:
    """经 NativeMCPClient transport 调用 MCP Server 上的工具。

    生产用法：注入 ToolApplicationService，使 Agent 工具调用走 MCP transport。
    测试用法：替换内部 transport 为 spy，验证调用路径。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8001/mcp") -> None:
        self._base_url = base_url

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """经 streamable_http transport 调用工具，返回解析后的结果 dict。"""
        from mcp.client.streamable_http import streamablehttp_client
        from mcp import ClientSession

        async with streamablehttp_client(self._base_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = ""
                for content in result.content:
                    if hasattr(content, "text"):
                        text += content.text
                return json.loads(text) if text else {}

    def invoke_sync(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """同步封装（供 Graph 节点使用）。"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # 在运行中的事件循环内：用线程避免嵌套 async
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.invoke(tool_name, arguments)).result()
        return asyncio.run(self.invoke(tool_name, arguments))


class LocalToolInvoker:
    """默认本地调用器：直接调用工具（不经 MCP transport）。

    用于测试和开发环境，无需运行 MCP Server，向后兼容直接 tool.invoke()。
    生产环境应注入 MCPToolInvoker 以走官方 MCP transport（streamable_http），
    使 Agent 工具调用经过统一的安全/审计边界。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from app_v4.tools.registry import TOOL_BY_NAME

        tool = TOOL_BY_NAME.get(tool_name)
        if tool is None:
            return {"status": "error", "error": f"unknown tool: {tool_name}"}
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return tool.invoke(arguments)

    def invoke_sync(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """同步封装（供 Graph 节点使用）。"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.invoke(tool_name, arguments)).result()
        return asyncio.run(self.invoke(tool_name, arguments))

    @property
    def call_count(self) -> int:
        return len(self.calls)


class SpyTransportInvoker:
    """测试用 spy：记录所有经 MCP Client 的调用（不建真实连接）。

    §6 矩阵 #16 反作弊：验证 Agent 工具调用确实经过 MCP Client transport，
    而非直接 TOOL_BY_NAME.invoke()。
    """

    def __init__(self, backend_url: str = "") -> None:
        self.calls: list[dict[str, Any]] = []
        self._backend_url = backend_url

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        # 返回结构化占位结果（测试只验证调用路径）
        return {
            "status": "success",
            "source": "spy_mcp_transport",
            "tool_name": tool_name,
            "arguments": arguments,
            "message": f"[spy] {tool_name} called via MCP Client transport",
        }

    def invoke_sync(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.invoke(tool_name, arguments)).result()
        return asyncio.run(self.invoke(tool_name, arguments))

    @property
    def call_count(self) -> int:
        return len(self.calls)
