"""MCP 单一生产路径测试。

覆盖：
  - 官方 Client initialize + tools/list + tools/call（独立 FastMCP HTTP Server）
  - MCPToolInvoker 经 streamable_http 调原生 MCP Server → 真实工具
  - build_dependencies 注入规则（mcp_server_url 非空 → MCPToolInvoker；空 → LocalToolInvoker）
  - 反作弊：Agent 工具调用必须经过注入的 invoker（SpyTransportInvoker）
  - confirm/deny 策略：confirm 无有效审批不得执行；deny 零工具调用
  - MCP 断连时 fail-closed（无本地降级、无工具执行）
"""

import asyncio
import socket
import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# 辅助：端口等待 / 独立 FastMCP Server 启动
# ---------------------------------------------------------------------------
def _wait_port_free(port: int, timeout: int = 5) -> None:
    """等待端口空闲（避免 TIME_WAIT 冲突）。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.2)


def _start_mcp_server(port: int):
    """在指定端口启动独立 FastMCP Server，返回 (server, serve_task)。"""
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
    """等待 uvicorn server 就绪。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if server.started:
            return True
        await asyncio.sleep(0.1)
    return False


def _run_native_mcp_test(port: int, test_fn):
    """辅助：在指定端口启动原生 MCP Server 并执行异步测试函数。"""
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    _wait_port_free(port, timeout=5)

    async def run():
        server, serve_task = _start_mcp_server(port)
        try:
            ready = await _wait_server_ready(server, timeout=5.0)
            assert ready, f"MCP Server 未在端口 {port} 就绪"
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await test_fn(session)
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serve_task, timeout=3)
            except Exception:
                pass

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# 官方 Client 完整生命周期
# ---------------------------------------------------------------------------
def test_native_mcp_full_lifecycle():
    """标准 MCP Server 完整生命周期：initialize + tools/list + tools/call。

    验证官方 MCP SDK + streamable_http transport 端到端可用。
    """

    async def full_check(session):
        # tools/list
        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        assert "disk_usage" in tool_names, f"disk_usage 不在工具列表: {tool_names}"
        assert "process_list" in tool_names
        assert len(tool_names) >= 7, f"工具数量不足: {len(tool_names)}"

        # tools/call disk_usage — 返回真实系统数据
        result = await session.call_tool("disk_usage", {"path": "."})
        text = "".join(c.text for c in result.content if hasattr(c, "text"))
        data = __import__("json").loads(text)
        assert data["status"] == "success"
        assert "used_percent" in data
        return True

    assert _run_native_mcp_test(18021, full_check) is True


# ---------------------------------------------------------------------------
# MCPToolInvoker 生产路径
# ---------------------------------------------------------------------------
def test_mcp_tool_invoker_hits_native_server():
    """生产路径验证：MCPToolInvoker 经 streamable_http 调原生 MCP Server → 真实工具。

    启动独立的原生 MCP Server 实例，用 MCPToolInvoker（生产注入的真实 invoker）调用，
    验证返回真实工具数据（含 _mcp_duration_ms 标记，证明经过原生 MCP 处理）。
    """
    from app_v4.mcp.agent_invoker import MCPToolInvoker

    port = 18023
    _wait_port_free(port, timeout=5)

    async def run():
        server, serve_task = _start_mcp_server(port)
        try:
            ready = await _wait_server_ready(server, timeout=5.0)
            assert ready, f"MCP Server 未在端口 {port} 就绪"
            invoker = MCPToolInvoker(base_url=f"http://127.0.0.1:{port}/mcp")
            result = await invoker.invoke("disk_usage", {"path": "."})
            return result
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serve_task, timeout=3)
            except Exception:
                pass

    data = asyncio.run(run())

    assert "_mcp_duration_ms" in data, (
        f"经 MCP transport 的结果应含 _mcp_duration_ms 标记: {list(data.keys())}"
    )
    assert data.get("status") == "success"
    assert "used_percent" in data, "原生 server 应返回真实磁盘数据"


# ---------------------------------------------------------------------------
# build_dependencies 注入规则
# ---------------------------------------------------------------------------
def test_build_dependencies_injects_mcp_tool_invoker_when_configured():
    """生产配置 mcp_server_url 时，build_dependencies 应注入 MCPToolInvoker。"""
    from app_v4.settings import Settings
    from app_v4.container import build_dependencies
    from app_v4.mcp.agent_invoker import MCPToolInvoker

    settings = Settings(
        use_fake_model=True,
        rate_limit_enabled=False,
        mcp_server_url="http://127.0.0.1:8001/mcp",
    )
    deps = build_dependencies(settings)
    assert isinstance(deps.mcp_invoker, MCPToolInvoker), (
        f"配置 mcp_server_url 后应注入 MCPToolInvoker，"
        f"实际 {type(deps.mcp_invoker).__name__}"
    )
    assert deps.mcp_invoker._base_url == "http://127.0.0.1:8001/mcp"


def test_build_dependencies_defaults_to_local_invoker_when_unconfigured():
    """未配置 mcp_server_url 时，build_dependencies 应保持 LocalToolInvoker。"""
    from app_v4.settings import Settings
    from app_v4.container import build_dependencies
    from app_v4.mcp.agent_invoker import LocalToolInvoker

    settings = Settings(use_fake_model=True, rate_limit_enabled=False)
    assert settings.mcp_server_url == "", "默认配置 mcp_server_url 应为空"

    deps = build_dependencies(settings)
    assert isinstance(deps.mcp_invoker, LocalToolInvoker), (
        f"未配置 mcp_server_url 时应为 LocalToolInvoker，"
        f"实际 {type(deps.mcp_invoker).__name__}"
    )


# ---------------------------------------------------------------------------
# 反作弊：Agent 工具调用必须经过注入的 invoker
# ---------------------------------------------------------------------------
def test_agent_tool_call_goes_through_mcp_invoker(isolated_deps):
    """§6 矩阵 #16 反作弊：Agent 工具调用必须经过 MCP invoker（不能直接 tool.invoke()）。"""
    from app_v4.mcp.agent_invoker import SpyTransportInvoker
    from app_v4.graph.runner import run_agent

    spy = SpyTransportInvoker()
    isolated_deps.mcp_invoker = spy

    result = run_agent("帮我分析磁盘", deps=isolated_deps)

    # 1. spy 记录了工具调用（证明 Agent 走了 invoker 路径）
    assert spy.call_count >= 1, "Agent 工具调用应经过 MCP invoker，但 spy 未记录到任何调用"
    tool_names = [c["tool_name"] for c in spy.calls]
    assert "disk_usage" in tool_names, f"期望 disk_usage 被调用，实际: {tool_names}"

    # 2. 返回结果来自 spy（source 标记证明路径）
    tool_calls = result.get("tool_calls", [])
    disk_calls = [c for c in tool_calls if c["tool_name"] == "disk_usage"]
    assert len(disk_calls) >= 1, "disk_usage 应在 tool_calls 中"
    assert disk_calls[0]["data"].get("source") == "spy_mcp_transport", (
        f"结果应来自 MCP transport，实际 source={disk_calls[0]['data'].get('source')}"
    )


def test_agent_mcp_invoker_receives_correct_arguments(isolated_deps):
    """反作弊：invoker 收到的参数必须与 plan 一致（防参数篡改）。"""
    from app_v4.mcp.agent_invoker import SpyTransportInvoker
    from app_v4.graph.runner import run_agent

    spy = SpyTransportInvoker()
    isolated_deps.mcp_invoker = spy

    result = run_agent("查询端口 5432", deps=isolated_deps)

    port_calls = [c for c in spy.calls if c["tool_name"] == "port_lookup"]
    assert len(port_calls) >= 1, f"port_lookup 应被调用，实际 calls: {spy.calls}"
    # 参数必须包含用户指定的端口号
    args = port_calls[0]["arguments"]
    assert args.get("port") == 5432, f"端口参数应为 5432，实际: {args}"


def test_agent_uses_injected_mcp_invoker_not_default(isolated_deps):
    """反作弊：验证 Agent 使用的是注入的 invoker，而非绕过它直接调 tool.invoke()。"""
    from app_v4.mcp.agent_invoker import SpyTransportInvoker
    from app_v4.graph.runner import run_agent

    spy = SpyTransportInvoker()
    isolated_deps.mcp_invoker = spy

    result = run_agent("帮我分析磁盘", deps=isolated_deps)

    disk_calls = [c for c in result.get("tool_calls", []) if c["tool_name"] == "disk_usage"]
    assert len(disk_calls) >= 1
    data = disk_calls[0]["data"]
    assert data.get("source") == "spy_mcp_transport", (
        "Agent 必须经过注入的 MCP invoker；若直接调 tool.invoke() 会返回真实系统数据"
    )
    assert "used_percent" not in data, (
        "spy 返回的假数据不应含 used_percent；若存在说明 Agent 绕过了 invoker"
    )


# ---------------------------------------------------------------------------
# confirm / deny 策略
# ---------------------------------------------------------------------------
def test_native_mcp_confirm_tool_requires_approval():
    """confirm 工具（service_restart）无有效审批时不得执行。

    直接经官方 Client 调用 confirm 工具，验证返回审批拒绝（不执行真实命令）。
    """

    async def check(session):
        result = await session.call_tool("service_restart", {"service": "sshd"})
        text = "".join(c.text for c in result.content if hasattr(c, "text"))
        data = __import__("json").loads(text)
        # confirm 工具无审批凭证 → 返回错误/拒绝，不执行
        assert data.get("status") in ("error", "rejected", "disabled"), (
            f"confirm 工具无审批时应拒绝，实际 status={data.get('status')}"
        )
        return data

    _run_native_mcp_test(18025, check)


def test_native_mcp_deny_tool_zero_invocations():
    """deny 工具（未注册 / file_delete 类）在工具列表中不存在，零调用。

    验证 tools/list 不暴露 deny 工具，且调用未知工具返回 isError。
    """

    async def check(session):
        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        # deny 工具不应出现在列表中
        assert "file_delete" not in tool_names, "deny 工具不应暴露给客户端"

        # 调用未知工具 → isError
        result = await session.call_tool("file_delete", {"path": "/etc/passwd"})
        assert result.isError, "未知/deny 工具调用应返回 isError"
        return True

    _run_native_mcp_test(18027, check)


# ---------------------------------------------------------------------------
# MCP 断连 fail-closed
# ---------------------------------------------------------------------------
def test_mcp_invoker_fail_closed_when_server_unreachable():
    """MCP 不可达时，MCPToolInvoker 必须结构化失败并 fail-closed，禁止静默回退本地工具。"""
    from app_v4.mcp.agent_invoker import MCPToolInvoker

    # 指向一个确定无服务监听的端口
    invoker = MCPToolInvoker(base_url="http://127.0.0.1:18099/mcp")

    async def run():
        return await invoker.invoke("disk_usage", {"path": "."})

    # 必须抛出异常（连接失败），而不是返回本地工具结果
    with pytest.raises(Exception) as exc_info:
        asyncio.run(run())

    # 确认不是本地降级（错误信息应涉及连接失败，而非本地工具执行）
    err_msg = str(exc_info.value).lower()
    assert "used_percent" not in err_msg, "fail-closed 不应返回本地工具结果"


# ---------------------------------------------------------------------------
# LocalToolInvoker 明确标记
# ---------------------------------------------------------------------------
def test_local_tool_invoker_returns_real_tool_data():
    """LocalToolInvoker 返回工具真实数据（含工具自身 source），不含 MCP 标记。

    与生产 MCP transport 的关键区别：本地路径结果不含 _mcp_duration_ms。
    """
    from app_v4.mcp.agent_invoker import LocalToolInvoker

    invoker = LocalToolInvoker()

    async def run():
        return await invoker.invoke("disk_usage", {"path": "."})

    result = asyncio.run(run())
    # 返回真实工具数据（disk_usage 设置 source=python.shutil）
    assert result.get("source") == "python.shutil", (
        f"LocalToolInvoker 应返回工具真实 source，实际 source={result.get('source')}"
    )
    assert "used_percent" in result, "LocalToolInvoker 应返回真实磁盘数据"
    # 不含 MCP transport 标记（与生产 MCP 路径区分）
    assert "_mcp_duration_ms" not in result, (
        "LocalToolInvoker 结果不应含 _mcp_duration_ms（仅 MCP transport 路径设置）"
    )
    assert invoker.call_count == 1
