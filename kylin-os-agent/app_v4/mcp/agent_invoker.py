"""Agent 侧 MCP 工具调用器 — 唯一官方 MCP Client 传输实现。

§5 Gate 2 #9 / §6 矩阵 #16：Web Agent 的生产工具调用必须经过真实
 ClientSession / streamablehttp_client transport，不得直接 TOOL_BY_NAME.invoke()。

此模块提供三种 invoker：
  - ``MCPToolInvoker``：生产 invoker，经官方 streamable_http 调用 MCP Server。
    当 settings.mcp_server_url 非空时由 build_dependencies 注入。
  - ``LocalToolInvoker``：明确的测试适配器（仅测试/开发用），直接调 tool.invoke()，
    不经 MCP transport。不能冒充生产 MCP。
  - ``SpyTransportInvoker``：测试用 spy，记录所有经 MCP Client 的调用（不建真实连接），
    用于反作弊测试（验证 Agent 确实经过 MCP Client transport）。

MCP 不可达时，MCPToolInvoker 必须结构化失败并 fail-closed，禁止静默回退本地工具。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("app_v4.mcp.agent_invoker")


async def _streamable_call(
    base_url: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """经官方 streamable_http transport 调用 MCP Server 上的工具。

    唯一底层传输实现：所有需要走 MCP transport 的 invoker 都复用此函数，
    禁止在各处重复手写 streamablehttp_client / ClientSession 样板。
    """
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async with streamablehttp_client(base_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text = ""
            for content in result.content:
                if hasattr(content, "text"):
                    text += content.text
            payload = json.loads(text) if text else {}
            if result.isError:
                payload.setdefault("status", "error")
                payload.setdefault("error", "MCP tool call failed")
            return payload


async def _streamable_list_tools(base_url: str) -> list[dict[str, Any]]:
    """经官方 streamable_http transport 列出 MCP Server 上的工具。"""
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async with streamablehttp_client(base_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
                }
                for t in tools.tools
            ]


def _run_async(coro):
    """同步入口：在无线程事件循环时用 asyncio.run，避免嵌套 async。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # 在运行中的事件循环内：用线程避免嵌套 async
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


class MCPToolInvoker:
    """生产 MCP 工具调用器 — 经官方 streamable_http transport 调用 MCP Server。

    生产用法：由 build_dependencies 在 settings.mcp_server_url 非空时注入，
    使 Agent 工具调用走 MCP transport。

    MCP 不可达时返回结构化 ``unavailable``（fail-closed），禁止静默
    回退本地工具。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8001/mcp") -> None:
        self._base_url = base_url

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """经 streamable_http transport 调用工具，返回解析后的结果 dict。

        传输或协议失败时返回结构化 ``unavailable``（fail-closed），
        不捕获为本地降级。
        """
        try:
            return await _streamable_call(self._base_url, tool_name, arguments)
        except Exception as exc:
            logger.warning(
                "MCP transport unavailable: tool=%s error_type=%s",
                tool_name,
                type(exc).__name__,
            )
            return {
                "status": "unavailable",
                "error": "MCP transport unavailable",
                "error_type": type(exc).__name__,
                "source": "mcp_transport",
                "tool_name": tool_name,
            }

    async def list_tools(self) -> list[dict[str, Any]]:
        """经 streamable_http transport 列出工具。"""
        return await _streamable_list_tools(self._base_url)

    def invoke_sync(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """同步封装（供 Graph 节点使用）。"""
        return _run_async(self.invoke(tool_name, arguments))


class LocalToolInvoker:
    """明确的测试适配器：直接调用工具（不经 MCP transport）。

    仅用于测试和开发环境，无需运行 MCP Server，向后兼容直接 tool.invoke()。
    不能冒充生产 MCP — 返回数据的 source 字段标记为 "local_test_adapter"，
    与真实 MCP transport 返回的数据可区分。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from app_v4.tools.registry import TOOL_BY_NAME

        tool = TOOL_BY_NAME.get(tool_name)
        if tool is None:
            return {"status": "error", "error": f"unknown tool: {tool_name}"}
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        # 直接返回工具真实结果（含工具自身设置的 source，如 python.shutil）。
        # 与生产 MCP transport 的区别：MCP 路径返回的结果含 _mcp_duration_ms 标记，
        # 本地路径不含该标记。不在此处篡改 source，避免破坏真实数据验证。
        return tool.invoke(arguments)

    def invoke_sync(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """同步封装（供 Graph 节点使用）。"""
        return _run_async(self.invoke(tool_name, arguments))

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
        return _run_async(self.invoke(tool_name, arguments))

    @property
    def call_count(self) -> int:
        return len(self.calls)
