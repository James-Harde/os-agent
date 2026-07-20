"""P2 MCP 协议层测试。"""

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
