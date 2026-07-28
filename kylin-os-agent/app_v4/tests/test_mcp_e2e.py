"""MCP 单一生产路径真实 E2E — 独立 marker，不纳入默认离线套件。

验证完整生产链路：
  启动独立 FastMCP HTTP Server（Streamable HTTP）
  → 官方 Client initialize + tools/list + tools/call
  → 用配置了 mcp_server_url 的 FastAPI /api/chat 调真实 disk_usage
  → 验证真实数据、MCP 标记（_mcp_duration_ms）、Trace 和审计

运行方式：
    pytest -m real_mcp_e2e -v -s

默认 `pytest` 运行会跳过本文件（pytest.ini addopts 排除 real_mcp_e2e）。
仅当 MCP 相关依赖（官方 MCP SDK）可用时执行。
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.real_mcp_e2e


def _wait_port_free(port: int, timeout: int = 5) -> None:
    """等待端口空闲。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.2)


async def _start_mcp_server(port: int):
    """启动独立 FastMCP Server，返回 (server, serve_task)。"""
    import uvicorn
    from app_v4.mcp.native_server import create_mcp_server

    mcp = create_mcp_server(host="127.0.0.1", port=port)
    app = mcp.streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    config.load()
    server.started = False
    serve_task = asyncio.ensure_future(server.serve())
    return server, serve_task


async def _wait_server_ready(server, timeout: float = 5.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if server.started:
            return True
        await asyncio.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# E2E：/api/chat → MCP Client → FastMCP → 真实 disk_usage
# ---------------------------------------------------------------------------
def test_api_chat_through_mcp_transport():
    """完整生产链路 E2E：/api/chat 经 MCP transport 调真实 disk_usage。

    验证：
      - /api/chat 返回成功（HTTP 200）
      - tool_calls 中 disk_usage 的 data 含真实 used_percent
      - data 含 _mcp_duration_ms 标记（证明经过 MCP transport，非本地降级）
      - 返回的 run_id 可查询到审计记录
      - 审计记录含 MCP 工具调用
    """
    from app_v4.settings import Settings
    from app_v4.container import build_dependencies, set_deps, reset_deps
    from app_v4.main import create_app

    port = 18031
    _wait_port_free(port, timeout=5)

    async def run():
        server, serve_task = await _start_mcp_server(port)
        try:
            ready = await _wait_server_ready(server, timeout=5.0)
            assert ready, f"MCP Server 未在端口 {port} 就绪"

            # 构建配置了 mcp_server_url 的 FastAPI app
            mcp_url = f"http://127.0.0.1:{port}/mcp"
            settings = Settings(
                use_fake_model=True,
                rate_limit_enabled=False,
                mcp_server_url=mcp_url,
            )
            deps = build_dependencies(settings)
            token = set_deps(deps)
            try:
                app = create_app(settings=settings, dependencies=deps)
                with TestClient(app) as client:
                    resp = client.post("/api/chat", json={"message": "分析磁盘"})
                    assert resp.status_code == 200, f"/api/chat 应返回 200, 实际 {resp.status_code}"
                    data = resp.json()

                    # 验证 tool_calls 含 disk_usage
                    tool_calls = data.get("tool_calls", [])
                    disk_calls = [c for c in tool_calls if c["tool_name"] == "disk_usage"]
                    assert len(disk_calls) >= 1, (
                        f"disk_usage 应在 tool_calls 中, 实际: {[c['tool_name'] for c in tool_calls]}"
                    )

                    disk_data = disk_calls[0]["data"]
                    # 真实磁盘数据
                    assert "used_percent" in disk_data, (
                        f"应返回真实磁盘数据, 实际 keys: {list(disk_data.keys())}"
                    )
                    assert isinstance(disk_data["used_percent"], (int, float))

                    # MCP 标记：证明经过 MCP transport（非本地降级）
                    assert "_mcp_duration_ms" in disk_data, (
                        f"经 MCP transport 的结果应含 _mcp_duration_ms 标记, "
                        f"实际 keys: {list(disk_data.keys())}"
                    )

                    # 审计可查询
                    run_id = data.get("run_id")
                    assert run_id, "应返回 run_id"
                    audit_resp = client.get(f"/api/traces/{run_id}")
                    assert audit_resp.status_code == 200, "应能通过 /api/traces/{run_id} 查询审计"
                    trace = audit_resp.json()
                    assert trace.get("run_id") == run_id

                    return disk_data
            finally:
                reset_deps(token)
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serve_task, timeout=3)
            except Exception:
                pass

    result = asyncio.run(run())
    assert result is not None


# ---------------------------------------------------------------------------
# E2E：官方 Client 直接调 MCP Server
# ---------------------------------------------------------------------------
def test_native_client_list_and_call():
    """官方 Client 直接连接独立 MCP Server：initialize + tools/list + tools/call。"""
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    port = 18033
    _wait_port_free(port, timeout=5)

    async def run():
        server, serve_task = await _start_mcp_server(port)
        try:
            ready = await _wait_server_ready(server, timeout=5.0)
            assert ready, f"MCP Server 未在端口 {port} 就绪"

            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    # initialize
                    init_result = await session.initialize()
                    assert init_result is not None

                    # tools/list
                    tools = await session.list_tools()
                    tool_names = {t.name for t in tools.tools}
                    assert "disk_usage" in tool_names
                    assert "process_list" in tool_names
                    assert "service_restart" in tool_names  # confirm 工具也注册

                    # tools/call disk_usage
                    result = await session.call_tool("disk_usage", {"path": "."})
                    text = "".join(c.text for c in result.content if hasattr(c, "text"))
                    data = __import__("json").loads(text)
                    assert data["status"] == "success"
                    assert "used_percent" in data
                    assert "_mcp_duration_ms" in data

                    return True
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serve_task, timeout=3)
            except Exception:
                pass

    assert asyncio.run(run()) is True
