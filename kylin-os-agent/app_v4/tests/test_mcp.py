"""P2 MCP 协议层测试 + Sprint 2 反作弊测试。"""

import pytest
from fastapi.testclient import TestClient
from app_v4.mcp.client import MCPClient


@pytest.fixture
def mcp_client() -> MCPClient:
    return MCPClient()


def test_tools_list(mcp_client: MCPClient):
    """tools/list 返回工具列表。"""
    resp = mcp_client.list_tools()
    assert "result" in resp
    assert "tools" in resp["result"]
    tools = resp["result"]["tools"]
    assert len(tools) >= 7  # 至少 7 个工具

    # 每个工具有必要字段
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t
        assert "riskLevel" in t
        assert "permission" in t


def test_call_disk_usage(mcp_client: MCPClient):
    """tools/call disk_usage 返回真实数据。"""
    resp = mcp_client.call_tool("disk_usage", {"path": "."})
    assert "result" in resp
    assert "content" in resp["result"]
    assert resp["result"]["isError"] is False

    # content[0].text 是 JSON
    text = resp["result"]["content"][0]["text"]
    data = __import__("json").loads(text)
    assert "used_percent" in data
    assert "_mcp_duration_ms" in data


def test_call_unknown_tool(mcp_client: MCPClient):
    """调用未知工具返回 isError。"""
    resp = mcp_client.call_tool("unknown_tool_xyz", {})
    assert "result" in resp
    assert resp["result"]["isError"] is True


def test_call_denied_tool(mcp_client: MCPClient):
    """deny 权限工具被拒绝。"""
    # service_restart 是 confirm（不是 deny），验证它被拒绝直接调用
    resp = mcp_client.call_tool("service_restart", {"service": "sshd"})
    assert "result" in resp
    assert resp["result"]["isError"] is True
    assert "requires approval" in resp["result"]["content"][0]["text"]


def test_mcp_error_invalid_method(mcp_client: MCPClient):
    """无效方法返回 JSON-RPC error。"""
    resp = mcp_client._server.handle({
        "jsonrpc": "2.0", "id": "x", "method": "nonexistent/method",
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_mcp_tool_list_contains_expected(mcp_client: MCPClient):
    """工具列表包含预期工具。"""
    resp = mcp_client.list_tools()
    names = [t["name"] for t in resp["result"]["tools"]]
    expected = {"disk_usage", "process_list", "port_lookup", "directory_usage"}
    assert expected.issubset(set(names))


# ---------------------------------------------------------------------------
# F3: mcp_endpoint 请求体解析错误处理
# ---------------------------------------------------------------------------
def test_mcp_endpoint_invalid_json_returns_400(client: TestClient):
    """请求体不是合法 JSON 时，应返回 400 + JSON-RPC Parse error（-32700）。"""
    resp = client.post(
        "/api/mcp",
        content="this is not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == -32700
    assert "not valid JSON" in data["error"]["message"]


def test_mcp_endpoint_empty_body_returns_400(client: TestClient):
    """空请求体时，应返回 400 + JSON-RPC Parse error（-32700）。"""
    resp = client.post(
        "/api/mcp",
        content="",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Phase D: 标准 MCP Server 集成测试（官方 SDK + streamable_http）
# ---------------------------------------------------------------------------
def _run_native_mcp_test(port: int, test_fn):
    """辅助：在指定端口启动原生 MCP Server 并执行测试函数。"""
    import asyncio
    import socket
    from app_v4.mcp.native_server import mcp
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    # 等待端口空闲（避免 TIME_WAIT 冲突）
    _wait_port_free(port, timeout=5)

    async def run():
        app = mcp.streamable_http_app()
        config = __import__("uvicorn").Config(
            app, host="127.0.0.1", port=port, log_level="warning")
        server = __import__("uvicorn").Server(config)
        # 允许端口复用
        config.load()
        server.started = False
        serve_task = asyncio.ensure_future(server.serve())
        # 等待 server 就绪
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.1)

        try:
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


def _wait_port_free(port: int, timeout: int = 5):
    """等待端口空闲。"""
    import time
    import socket
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# Sprint 2 反作弊：Agent 工具调用必须经过 MCP Client transport
# ---------------------------------------------------------------------------
def test_agent_tool_call_goes_through_mcp_invoker(isolated_deps):
    """§6 矩阵 #16 反作弊：Agent 工具调用必须经过 MCP invoker（不能直接 tool.invoke()）。

    注入 SpyTransportVerifier 替代默认 LocalToolInvoker，验证：
      - Agent 规划并执行 disk_usage 时，调用被 spy 记录
      - 返回结果来自 spy（source=spy_mcp_transport），证明路径经过 MCP Client
    """
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
    """反作弊：验证 Agent 使用的是注入的 invoker，而非绕过它直接调 tool.invoke()。

    通过对比：注入 spy 后，真实 tool.invoke() 不应被 Agent 直接调用
    （spy 返回假数据，若 Agent 直接调 tool 会返回真实系统数据）。
    """
    from app_v4.mcp.agent_invoker import SpyTransportInvoker
    from app_v4.graph.runner import run_agent

    spy = SpyTransportInvoker()
    isolated_deps.mcp_invoker = spy

    result = run_agent("帮我分析磁盘", deps=isolated_deps)

    # 若 Agent 绕过 invoker 直接调 tool.invoke()，data 会含真实 used_percent 数字；
    # 若走 spy，data.source == "spy_mcp_transport" 且无 used_percent。
    disk_calls = [c for c in result.get("tool_calls", []) if c["tool_name"] == "disk_usage"]
    assert len(disk_calls) >= 1
    data = disk_calls[0]["data"]
    assert data.get("source") == "spy_mcp_transport", (
        "Agent 必须经过注入的 MCP invoker；若直接调 tool.invoke() 会返回真实系统数据"
    )
    assert "used_percent" not in data, (
        "spy 返回的假数据不应含 used_percent；若存在说明 Agent 绕过了 invoker"
    )


def test_mcp_tool_invoker_hits_native_server():
    """生产路径验证：MCPToolInvoker 经 streamable_http 调原生 MCP Server → 真实工具。

    启动独立的原生 MCP Server 实例（避免与模块级单例 session manager 冲突），
    用 MCPToolInvoker（生产注入的真实 invoker）调用，
    验证返回真实工具数据（含 _mcp_duration_ms 标记，证明经过原生 MCP 处理）。
    """
    import asyncio
    import uvicorn
    from mcp.server import FastMCP
    from app_v4.mcp.native_server import register_tools
    from app_v4.mcp.agent_invoker import MCPToolInvoker

    # 创建独立的 FastMCP 实例（模块级 mcp 单例的 session manager 只能 run 一次）
    from app_v4.tools.application import ToolApplicationService
    local_mcp = FastMCP("kylin-secure-os-agent-e2e")
    local_app_service = ToolApplicationService()
    from app_v4.tools.registry import TOOL_BY_NAME, get_tools
    from app_v4.mcp.native_server import _make_handler, _tool_input_schema
    for tool in get_tools():
        tool_obj = TOOL_BY_NAME.get(tool.name)
        if tool_obj is None:
            continue
        schema = _tool_input_schema(tool_obj)
        handler = _make_handler(tool.name, tool_obj, schema, local_app_service)
        local_mcp.add_tool(
            fn=handler,
            name=tool.name,
            description=tool.description or f"工具: {tool.name}",
            structured_output=False,
        )

    port = 18023
    _wait_port_free(port, timeout=5)

    async def run():
        app = local_mcp.streamable_http_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        config.load()
        server.started = False
        serve_task = asyncio.ensure_future(server.serve())
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.1)
        try:
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


def test_build_dependencies_injects_mcp_tool_invoker_when_configured():
    """生产配置 mcp_server_url 时，build_dependencies 应注入 MCPToolInvoker。

    证据：§6 矩阵 #16 / HANDOFF 下一步 #3 — 生产启动根据配置注入
    MCPToolInvoker，不再一直默认 LocalToolInvoker。
    """
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
    # 注入的 invoker 应指向配置的 URL
    assert deps.mcp_invoker._base_url == "http://127.0.0.1:8001/mcp"


def test_build_dependencies_defaults_to_local_invoker_when_unconfigured():
    """未配置 mcp_server_url 时，build_dependencies 应保持 LocalToolInvoker。

    避免测试/开发环境访问网络；向后兼容现有 144 测试（默认 LocalToolInvoker）。
    """
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


def test_native_mcp_full_lifecycle():
    """标准 MCP Server 完整生命周期：initialize + tools/list + tools/call。

    修复 audit #11：验证官方 MCP SDK + streamable_http transport 端到端可用。
    单测试内串行执行多个检查，避免多 uvicorn 实例端口竞争。
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
