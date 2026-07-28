"""MCP 单一生产路径真实 E2E — 独立 marker，不纳入默认离线套件。

验证完整生产链路：
  启动独立 FastMCP HTTP Server（Streamable HTTP，后台线程）
  → 官方 Client initialize + tools/list + tools/call
  → 用配置了 mcp_server_url 的 FastAPI /api/chat 调真实 disk_usage
  → 验证真实数据、MCP 标记（_mcp_duration_ms）、Trace 和审计

运行方式：
    pytest -m real_mcp_e2e -v -s

默认 `pytest` 运行会跳过本文件（pytest.ini addopts 排除 real_mcp_e2e）。
"""

from __future__ import annotations

import asyncio
import socket
import threading
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


def _wait_port_listening(port: int, timeout: float = 5.0) -> bool:
    """等待端口有服务监听。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


class _McpServerThread:
    """在后台线程中运行独立 FastMCP Server（自己的事件循环）。"""

    def __init__(self, port: int) -> None:
        self.port = port
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None

    def start(self) -> None:
        _wait_port_free(self.port, timeout=5)
        ready = threading.Event()
        error: list[BaseException] = []

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                import uvicorn
                from app_v4.mcp.native_server import create_mcp_server

                mcp = create_mcp_server(host="127.0.0.1", port=self.port)
                app = mcp.streamable_http_app()
                config = uvicorn.Config(
                    app, host="127.0.0.1", port=self.port, log_level="warning",
                )
                self._server = uvicorn.Server(config)
                self._loop.run_until_complete(self._server.serve())
            except BaseException as exc:
                error.append(exc)
            finally:
                ready.set()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

        # 等待端口就绪
        if not _wait_port_listening(self.port, timeout=8.0):
            if error:
                raise RuntimeError(f"MCP Server 启动失败: {error[0]}")
            raise RuntimeError(f"MCP Server 未在端口 {self.port} 就绪")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture
def mcp_server_18031():
    """在端口 18031 启动独立 MCP Server。"""
    srv = _McpServerThread(18031)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def mcp_server_18033():
    """在端口 18033 启动独立 MCP Server。"""
    srv = _McpServerThread(18033)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# E2E：/api/chat → MCP Client → FastMCP → 真实 disk_usage
# ---------------------------------------------------------------------------
def test_api_chat_through_mcp_transport(mcp_server_18031):
    """完整生产链路 E2E：/api/chat 经 MCP transport 调真实 disk_usage。

    验证：
      - /api/chat 返回成功（HTTP 200）
      - tool_calls 中 disk_usage 的 data 含真实 used_percent
      - data 含 _mcp_duration_ms 标记（证明经过 MCP transport，非本地降级）
      - 返回的 run_id 可查询到审计记录
    """
    from app_v4.settings import Settings
    from app_v4.container import build_dependencies, set_deps, reset_deps
    from app_v4.main import create_app

    mcp_url = f"http://127.0.0.1:18031/mcp"
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
    finally:
        reset_deps(token)


# ---------------------------------------------------------------------------
# E2E：官方 Client 直接调 MCP Server
# ---------------------------------------------------------------------------
def test_native_client_list_and_call(mcp_server_18033):
    """官方 Client 直接连接独立 MCP Server：initialize + tools/list + tools/call。

    验证：
      - auto 只读工具暴露（disk_usage / process_list）
      - mutation 工具（service_restart）不暴露（最小权限）
      - tools/call disk_usage 返回真实数据 + _mcp_duration_ms
    """
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async def run():
        async with streamablehttp_client("http://127.0.0.1:18033/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                # initialize
                init_result = await session.initialize()
                assert init_result is not None

                # tools/list
                tools = await session.list_tools()
                tool_names = {t.name for t in tools.tools}
                assert "disk_usage" in tool_names
                assert "process_list" in tool_names
                # 最小权限：confirm/mutation 工具不暴露
                assert "service_restart" not in tool_names, (
                    f"mutation 工具不应暴露, 实际列表: {tool_names}"
                )

                # tools/call disk_usage
                result = await session.call_tool("disk_usage", {"path": "."})
                text = "".join(c.text for c in result.content if hasattr(c, "text"))
                data = __import__("json").loads(text)
                assert data["status"] == "success"
                assert "used_percent" in data
                assert "_mcp_duration_ms" in data

                return True

    # 在独立事件循环中运行（避免与 pytest-asyncio 冲突）
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(run()) is True
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# E2E：/api/chat → MCP → 真实 disk_usage + 结构化 metadata + 共享审计
# ---------------------------------------------------------------------------
def test_api_chat_mcp_structured_metadata_and_shared_audit():
    """验证 /api/chat 经 MCP 调 disk_usage 返回结构化 metadata 且写入审计。

    显式把测试隔离的 AuditLogger 注入 MCP Server，证明 MCP 审计和 Agent
    使用同一可注入 AuditLogger（共享审计边界）。
    """
    import tempfile
    from pathlib import Path

    from app_v4.settings import Settings
    from app_v4.container import build_dependencies, set_deps, reset_deps
    from app_v4.main import create_app
    from app_v4.audit.logger import AuditLogger
    from app_v4.mcp.native_server import create_mcp_server

    # 独立端口 + 独立审计 DB
    port = 18041
    _wait_port_free(port, timeout=5)
    tmp_dir = Path(tempfile.mkdtemp(prefix="appv4_mcp_shared_"))
    shared_audit = AuditLogger(db_path=str(tmp_dir / "audit.db"))

    # 用共享审计创建 MCP Server
    mcp = create_mcp_server(audit_logger=shared_audit, host="127.0.0.1", port=port)
    mcp_srv = _McpServerThread.__new__(_McpServerThread)
    mcp_srv.port = port
    mcp_srv._thread = None
    mcp_srv._loop = None
    mcp_srv._server = None

    # 手动启动（复用 _McpServerThread 逻辑，但注入自定义 mcp）
    _start_custom_mcp_server(mcp_srv, mcp, port)
    try:
        mcp_url = f"http://127.0.0.1:{port}/mcp"
        settings = Settings(
            use_fake_model=True,
            rate_limit_enabled=False,
            mcp_server_url=mcp_url,
            db_path=str(tmp_dir / "agent_v4.db"),
        )
        deps = build_dependencies(settings)
        # 关键：Agent 也使用同一个 shared_audit
        deps._audit_logger = shared_audit
        token = set_deps(deps)
        try:
            app = create_app(settings=settings, dependencies=deps)
            with TestClient(app) as client:
                # 审计调用前
                audit_before = len(shared_audit.list_logs(limit=1000))

                resp = client.post("/api/chat", json={"message": "分析磁盘"})
                assert resp.status_code == 200, f"/api/chat 应返回 200, 实际 {resp.status_code}"
                data = resp.json()

                # tool_calls 含 disk_usage
                tool_calls = data.get("tool_calls", [])
                disk_calls = [c for c in tool_calls if c["tool_name"] == "disk_usage"]
                assert len(disk_calls) >= 1, (
                    f"disk_usage 应在 tool_calls 中, 实际: {[c['tool_name'] for c in tool_calls]}"
                )

                disk_data = disk_calls[0]["data"]
                # 真实磁盘数据
                assert "used_percent" in disk_data
                assert isinstance(disk_data["used_percent"], (int, float))

                # MCP 标记：证明经过 MCP transport（非本地降级）
                assert "_mcp_duration_ms" in disk_data

                # 结构化 metadata：invocation_id 存在且唯一
                invocation_id = disk_data.get("invocation_id")
                assert invocation_id, "结果应含 invocation_id"
                assert invocation_id.startswith("mcp:disk_usage:"), (
                    f"invocation_id 格式应为 mcp:disk_usage:<uuid>, 实际 {invocation_id}"
                )

                # 审计新增（MCP 审计和 Agent 使用同一 AuditLogger）
                audit_after = len(shared_audit.list_logs(limit=1000))
                assert audit_after > audit_before, (
                    f"/api/chat 经 MCP 后审计应新增, before={audit_before}, after={audit_after}"
                )

                # 验证 MCP 工具调用审计写入（intent = mcp_call:disk_usage）
                mcp_logs = [l for l in shared_audit.list_logs(limit=1000)
                            if l["intent"] == "mcp_call:disk_usage"]
                assert len(mcp_logs) >= 1, "MCP 工具调用应写入审计 (intent=mcp_call:disk_usage)"

                # 验证 run_id 可查询
                run_id = data.get("run_id")
                assert run_id, "应返回 run_id"
                audit_resp = client.get(f"/api/traces/{run_id}")
                assert audit_resp.status_code == 200
        finally:
            reset_deps(token)
    finally:
        mcp_srv.stop()


def _start_custom_mcp_server(mcp_srv, mcp, port: int) -> None:
    """启动自定义 FastMCP 实例到 _McpServerThread。"""
    import uvicorn

    ready = threading.Event()
    error: list[BaseException] = []

    def _run():
        mcp_srv._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(mcp_srv._loop)
        try:
            app = mcp.streamable_http_app()
            config = uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="warning",
            )
            mcp_srv._server = uvicorn.Server(config)
            mcp_srv._loop.run_until_complete(mcp_srv._server.serve())
        except BaseException as exc:
            error.append(exc)
        finally:
            ready.set()

    mcp_srv._thread = threading.Thread(target=_run, daemon=True)
    mcp_srv._thread.start()

    if not _wait_port_listening(port, timeout=8.0):
        if error:
            raise RuntimeError(f"MCP Server 启动失败: {error[0]}")
        raise RuntimeError(f"MCP Server 未在端口 {port} 就绪")
