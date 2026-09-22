"""前端工具目录 API 测试。"""


def test_tools_api_lists_local_registry(client):
    from app_v4.mcp.agent_invoker import LocalToolInvoker

    client.app.state.deps.mcp_invoker = LocalToolInvoker()
    response = client.get("/api/tools")

    assert response.status_code == 200
    tools = response.json()["tools"]
    disk = next(tool for tool in tools if tool["name"] == "disk_usage")
    assert disk["permission"] == "auto"
    assert disk["description"]
    assert disk["inputSchema"]["type"] == "object"
