"""P3 限流 + 循环熔断补充测试。

覆盖：
  - 测试环境关闭限流（APP_V4_DISABLE_RATE_LIMIT）时不限流
  - 生产模式下 SlowAPIMiddleware 注册 + 限流触发 429
  - §4.2 #3：循环检测仅限同 run，用户重复合法问题必须允许执行

限流测试直接打真实生产 app 路由（通过 create_app 启用限流），
不另建临时 FastAPI app（反作弊规则 #9）。
"""

import os

import pytest
from fastapi.testclient import TestClient


def test_chat_not_rate_limited_in_test_env(client: TestClient):
    """测试环境关闭限流：连续请求 12 次都应 200。"""
    for i in range(12):
        resp = client.post("/api/chat", json={"message": f"你好 {i}"})
        assert resp.status_code == 200, f"请求 {i} 被限流：{resp.status_code}"


def test_rate_limit_middleware_is_present():
    """生产模式下，SlowAPIMiddleware 应被注册（提供限流执行能力）。"""
    os.environ.pop("APP_V4_DISABLE_RATE_LIMIT", None)
    os.environ["APP_V4_USE_FAKE_MODEL"] = "true"
    import importlib
    import app_v4.main as main_mod
    importlib.reload(main_mod)
    cls_names = []
    for c in main_mod.app.user_middleware:
        cls = getattr(c, "cls", None)
        cls_names.append(cls.__name__ if cls is not None else c.__class__.__name__)
    assert "SlowAPIMiddleware" in cls_names, f"缺少限流中间件: {cls_names}"
    assert getattr(main_mod.app.state, "limiter", None) is not None


def test_rate_limit_triggers_on_real_app():
    """生产模式（启用限流）下，超过容量应返回 429。

    通过 create_app 构建一个启用限流的真实 app，直接打 /api/chat 路由，
    不另建临时 FastAPI app。使用隔离容器避免污染全局 DB。
    """
    import tempfile
    from pathlib import Path

    from app_v4.main import create_app
    from app_v4.settings import Settings
    from app_v4.container import build_dependencies, set_deps, reset_deps

    db_dir = Path(tempfile.mkdtemp(prefix="appv4_rl_"))
    settings = Settings(
        use_fake_model=True,
        rate_limit_enabled=True,
        db_path=str(db_dir / "agent_v4.db"),
    )
    deps = build_dependencies(settings)
    token = set_deps(deps)
    try:
        app = create_app(settings=settings, dependencies=deps)
        c = TestClient(app)

        count_429 = 0
        for i in range(12):
            r = c.post("/api/chat", json={"message": f"你好 {i}"})
            if r.status_code == 429:
                count_429 += 1
        assert count_429 >= 1, f"12 次请求（10/minute）应至少触发 1 次 429，实际 {count_429}"
    finally:
        reset_deps(token)


def test_repeat_legal_turn_not_flagged_as_loop(client: TestClient):
    """§4.2 #3：循环检测仅限同 run 内部；用户重复合法问题必须允许执行。"""
    resp1 = client.post("/api/chat", json={"message": "分析磁盘"})
    assert resp1.status_code == 200
    thread_id = resp1.json()["thread_id"]
    assert resp1.json()["guard_decision"] == "allow"

    resp2 = client.post("/api/chat", json={"message": "分析磁盘", "thread_id": thread_id})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["guard_decision"] == "allow", f"假循环! reasons={data2.get('guard_reasons')}"
    names2 = [c["tool_name"] for c in data2["tool_calls"]]
    assert "disk_usage" in names2, f"第二轮应调用 disk_usage，得到 {names2}"
