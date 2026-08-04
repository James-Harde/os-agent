"""异步非流式入口与依赖容器隔离的聚焦回归。"""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app_v4.container import (
    Dependencies,
    build_dependencies,
    get_deps,
    reset_deps,
    set_deps,
)
from app_v4.graph.runner import arun_agent, run_agent
from app_v4.main import create_app
from app_v4.settings import Settings


class _CountingModel:
    """代理真实 fake model，并记录模型调用时实际可见的 Dependencies。"""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.call_count = 0
        self.seen_deps: list[Dependencies] = []

    async def astream(self, messages, **kwargs):
        self.call_count += 1
        self.seen_deps.append(get_deps())
        await asyncio.sleep(0)
        async for chunk in self.delegate.astream(messages, **kwargs):
            self.seen_deps.append(get_deps())
            yield chunk


def _build_counted_deps(db_path: Path) -> tuple[Dependencies, _CountingModel]:
    settings = Settings(
        use_fake_model=True,
        rate_limit_enabled=False,
        db_path=str(db_path),
        kill_switch=False,
    )
    deps = build_dependencies(settings)
    model = _CountingModel(deps.model)
    deps._model = model
    return deps, model


def test_arun_agent_uses_request_deps_for_entire_await(tmp_path: Path) -> None:
    """显式 B 必须覆盖调用方 A，且 await 完成后恢复原有 A 上下文。"""

    deps_a, model_a = _build_counted_deps(tmp_path / "ambient-a.db")
    deps_b, model_b = _build_counted_deps(tmp_path / "request-b.db")

    async def exercise() -> dict:
        token = set_deps(deps_a)
        try:
            assert get_deps() is deps_a
            result = await arun_agent("你好", deps=deps_b)
            assert get_deps() is deps_a
            return result
        finally:
            reset_deps(token)

    result = asyncio.run(exercise())

    assert result["answer"]
    assert model_a.call_count == 0, "请求 B 不得调用调用方/全局 A 容器的模型"
    assert model_b.call_count > 0, "请求注入的 B 容器模型必须被实际调用"
    assert model_b.seen_deps
    assert all(seen is deps_b for seen in model_b.seen_deps)


def test_run_agent_is_a_stable_sync_adapter(tmp_path: Path) -> None:
    """同步入口始终返回 dict，并可跨多个 asyncio.run 事件循环重复调用。"""

    deps, _ = _build_counted_deps(tmp_path / "sync-adapter.db")

    first = run_agent("你好", deps=deps)
    second = run_agent("你好", deps=deps)

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert not inspect.isawaitable(first)
    assert not inspect.isawaitable(second)


def test_run_agent_rejects_a_running_event_loop(tmp_path: Path) -> None:
    """异步调用方必须 await arun_agent，run_agent 不得偷偷返回 coroutine。"""

    deps, _ = _build_counted_deps(tmp_path / "running-loop.db")

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="arun_agent"):
            run_agent("你好", deps=deps)

    asyncio.run(exercise())


def test_dependencies_reset_clears_async_lock(tmp_path: Path) -> None:
    """容器 reset 必须同时丢弃事件循环绑定的异步锁引用。"""

    deps, _ = _build_counted_deps(tmp_path / "reset-lock.db")
    deps._ainvoke_lock = asyncio.Lock()

    deps.reset()

    assert deps._ainvoke_lock is None


def test_aclose_is_idempotent_and_nulls_refs(tmp_path: Path) -> None:
    """aclose() 必须幂等：重复调用不报错，且关闭后 checkpointer 引用清空。"""

    deps, _ = _build_counted_deps(tmp_path / "aclose-idem.db")

    async def _drive() -> None:
        # 触发异步 checkpointer 懒建（打开 aiosqlite 连接 + worker 线程）
        cp = await deps.get_async_checkpointer()
        assert deps._async_checkpointer is cp
        # 第一次关闭
        await deps.aclose()
        assert deps._async_checkpointer is None
        assert deps._checkpointer is None
        # 第二次关闭（幂等）：已 None，不得报错
        await deps.aclose()
        assert deps._async_checkpointer is None

    asyncio.run(_drive())


def test_two_apps_aclose_are_independent(tmp_path: Path) -> None:
    """两个 app 各自持有独立的 AsyncSqliteSaver；关闭 A 不影响 B 的连接。"""

    deps_a, _ = _build_counted_deps(tmp_path / "indep-a.db")
    deps_b, _ = _build_counted_deps(tmp_path / "indep-b.db")

    async def _drive() -> object:
        cp_a = await deps_a.get_async_checkpointer()
        cp_b = await deps_b.get_async_checkpointer()
        # 两个容器持有不同实例（资源独立）
        assert cp_a is not cp_b
        assert deps_a._async_checkpointer is cp_a
        assert deps_b._async_checkpointer is cp_b
        # 关闭 A
        await deps_a.aclose()
        assert deps_a._async_checkpointer is None
        # B 的引用与实例保持完整
        assert deps_b._async_checkpointer is cp_b
        return cp_b

    cp_b = asyncio.run(_drive())

    # 退出后 B 仍可独立关闭
    async def _close_b() -> None:
        await deps_b.aclose()
        assert deps_b._async_checkpointer is None

    asyncio.run(_close_b())
    # 防止 lint 未使用告警（cp_b 仅用于身份断言）
    assert cp_b is not None


def test_two_apps_two_databases_remain_isolated_under_concurrency(
    tmp_path: Path,
) -> None:
    """两个 app 并发请求只调用各自模型，并只写入各自审计数据库。"""

    deps_a, model_a = _build_counted_deps(tmp_path / "app-a.db")
    deps_b, model_b = _build_counted_deps(tmp_path / "app-b.db")
    app_a = create_app(settings=deps_a.settings, dependencies=deps_a)
    app_b = create_app(settings=deps_b.settings, dependencies=deps_b)

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(client_a.post, "/api/chat", json={"message": "你好"}),
                pool.submit(client_b.post, "/api/chat", json={"message": "你好"}),
                pool.submit(client_a.post, "/api/chat", json={"message": "查看进程"}),
                pool.submit(client_b.post, "/api/chat", json={"message": "分析磁盘"}),
            ]
            responses = [future.result() for future in futures]

    assert all(response.status_code == 200 for response in responses), [
        response.text for response in responses
    ]

    response_a_ids = {responses[index].json()["run_id"] for index in (0, 2)}
    response_b_ids = {responses[index].json()["run_id"] for index in (1, 3)}
    audit_a_ids = {
        item["run_id"] for item in deps_a.audit_logger.list_logs(limit=20)
    }
    audit_b_ids = {
        item["run_id"] for item in deps_b.audit_logger.list_logs(limit=20)
    }

    assert model_a.call_count > 0
    assert model_b.call_count > 0
    assert all(seen is deps_a for seen in model_a.seen_deps)
    assert all(seen is deps_b for seen in model_b.seen_deps)
    assert response_a_ids <= audit_a_ids
    assert response_b_ids <= audit_b_ids
    assert response_a_ids.isdisjoint(audit_b_ids)
    assert response_b_ids.isdisjoint(audit_a_ids)
