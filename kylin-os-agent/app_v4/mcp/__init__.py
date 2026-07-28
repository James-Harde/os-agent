"""MCP 子包 — 官方 FastMCP + Streamable HTTP 唯一生产路径。

公开入口：
  - :func:`create_mcp_server`：可注入的 FastMCP server 工厂
  - :class:`MCPToolInvoker`：生产 MCP Client（streamable_http）
  - :class:`LocalToolInvoker`：明确的测试适配器（不经 MCP transport）
  - :class:`SpyTransportInvoker`：测试用 spy（反作弊验证调用路径）
"""

from app_v4.mcp.native_server import create_mcp_server, mcp, run_streamable_http
from app_v4.mcp.agent_invoker import MCPToolInvoker, LocalToolInvoker, SpyTransportInvoker

__all__ = [
    "create_mcp_server",
    "mcp",
    "run_streamable_http",
    "MCPToolInvoker",
    "LocalToolInvoker",
    "SpyTransportInvoker",
]
