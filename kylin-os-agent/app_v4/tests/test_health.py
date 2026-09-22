"""健康接口应明确区分服务在线与模型调用模式。"""


def test_health_exposes_non_secret_model_mode(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["engine"] == "langgraph"
    assert data["model_mode"] == "fake"
    assert data["model_name"] == "fake-chat-model"
    assert "api_key" not in data
