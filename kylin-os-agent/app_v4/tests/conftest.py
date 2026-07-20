"""共享 fixtures。"""

import os
import pytest


@pytest.fixture(autouse=True)
def _use_fake_model(monkeypatch):
    """所有测试默认使用 fake model（不调用真实 LLM）。"""
    monkeypatch.setenv("APP_V4_USE_FAKE_MODEL", "true")


@pytest.fixture()
def client(monkeypatch):
    """FastAPI TestClient（测试时限流关闭）。"""
    monkeypatch.setenv("APP_V4_USE_FAKE_MODEL", "true")
    monkeypatch.setenv("APP_V4_DISABLE_RATE_LIMIT", "true")
    from fastapi.testclient import TestClient
    from app_v4.main import app
    return TestClient(app)
