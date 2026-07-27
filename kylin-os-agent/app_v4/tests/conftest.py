"""共享 fixtures — 提供真实隔离的测试环境。

关键设计：
  - 每个集成测试使用独立的临时数据库（tmp_path），不依赖 data/agent_v4.db。
  - 通过 container.set_deps() 注入隔离的 Dependencies，避免全局单例污染。
  - 默认使用确定性假模型（APP_V4_USE_FAKE_MODEL=true）。
  - 测试结束后 reset_deps 恢复上下文。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# 在任何 app_v4 导入前，确保测试环境变量就位
os.environ.setdefault("APP_V4_USE_FAKE_MODEL", "true")
os.environ.setdefault("APP_V4_DISABLE_RATE_LIMIT", "true")


@pytest.fixture(autouse=True)
def _use_fake_model(monkeypatch):
    """所有测试默认使用 fake model（不调用真实 LLM）。"""
    monkeypatch.setenv("APP_V4_USE_FAKE_MODEL", "true")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """FastAPI TestClient（隔离临时 DB + 关闭限流）。

    每个测试获得独立的 SQLite 数据库，彻底隔离 checkpoint / 审计 / 记忆。
    """
    monkeypatch.setenv("APP_V4_USE_FAKE_MODEL", "true")
    monkeypatch.setenv("APP_V4_DISABLE_RATE_LIMIT", "true")

    # 构建隔离的 settings + 容器
    from app_v4.settings import Settings
    from app_v4.container import build_dependencies, set_deps, reset_deps

    db_dir = Path(tempfile.mkdtemp(prefix="appv4_test_"))
    settings = Settings(
        use_fake_model=True,
        rate_limit_enabled=False,
        db_path=str(db_dir / "agent_v4.db"),
        kill_switch=False,
    )
    deps = build_dependencies(settings)
    token = set_deps(deps)
    try:
        from fastapi.testclient import TestClient
        from app_v4.main import create_app

        app = create_app(settings=settings, dependencies=deps)
        with TestClient(app) as test_client:
            yield test_client
    finally:
        reset_deps(token)


@pytest.fixture()
def isolated_deps(monkeypatch, tmp_path):
    """供非 HTTP 测试使用的隔离依赖容器。"""
    monkeypatch.setenv("APP_V4_USE_FAKE_MODEL", "true")
    from app_v4.settings import Settings
    from app_v4.container import build_dependencies, set_deps, reset_deps

    db_dir = Path(tempfile.mkdtemp(prefix="appv4_isolated_"))
    settings = Settings(
        use_fake_model=True,
        rate_limit_enabled=False,
        db_path=str(db_dir / "agent_v4.db"),
    )
    deps = build_dependencies(settings)
    token = set_deps(deps)
    try:
        yield deps
    finally:
        reset_deps(token)
