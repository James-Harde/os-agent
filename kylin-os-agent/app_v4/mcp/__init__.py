"""MCP 子包 — 官方 FastMCP + Streamable HTTP 唯一生产路径。

Server 入口请从 ``app_v4.mcp.native_server`` 显式导入，避免执行
``python -m app_v4.mcp.native_server`` 时由包初始化提前加载 Server 模块。

公开入口：
  - :class:`MCPToolInvoker`：生产 MCP Client（streamable_http）
  - :class:`LocalToolInvoker`：明确的测试适配器（不经 MCP transport）
  - :class:`SpyTransportInvoker`：测试用 spy（反作弊验证调用路径）
"""

from app_v4.mcp.agent_invoker import MCPToolInvoker, LocalToolInvoker, SpyTransportInvoker

__all__ = [
    "MCPToolInvoker",
    "LocalToolInvoker",
    "SpyTransportInvoker",
]
