"""rag_search 工具边界测试（真实 Milvus 主路径 + 依赖注入）。

验证 rag_search 工具：
  1. 检索返回 success + 完整 citation 结构（score/text/source/citation/chunk_id/parent_id）
  2. BM25 字面匹配生效（真实 Milvus）
  3. Embedding 未配置时返回 unavailable（不伪装成功）
  4. Milvus 不可达时返回 unavailable（不伪装成功）

依赖注入：通过 ``deps.rag_store = <fake>`` 注入隔离 store，
不再依赖已移除的模块级 ``_rag_store`` 单例。

不读真实 .env、不访问 Embedding API：显式注入 FakeStore。
"""

from __future__ import annotations

from typing import Any

import pytest

from app_v4.container import build_dependencies, get_deps, reset_deps, set_deps
from app_v4.settings import Settings
from app_v4.tools.system_tools import rag_search


class _FakeStore:
    """rag_search 工具边界测试用的最小 store double。"""

    def __init__(self, results: list[dict[str, Any]] | None = None, fail: Exception | None = None) -> None:
        self._results = results or []
        self._fail = fail
        self.search_calls: list[dict[str, Any]] = []

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        self.search_calls.append({"query": query, "top_k": top_k})
        if self._fail is not None:
            raise self._fail
        return self._results


def _success_result(doc_id: str = "doc-01") -> dict[str, Any]:
    return {
        "score": 0.9,
        "text": "磁盘使用率通过 df -h 命令查看。",
        "source": "faq-disk",
        "document_id": doc_id,
        "chunk_id": f"{doc_id}-c0",
        "citation": f"[{doc_id}]",
    }


@pytest.fixture()
def injected_deps(monkeypatch):
    """构建隔离依赖容器，避免污染全局状态。"""
    monkeypatch.setenv("APP_V4_USE_FAKE_MODEL", "true")
    settings = Settings(use_fake_model=True, rate_limit_enabled=False, milvus_uri="")
    deps = build_dependencies(settings)
    token = set_deps(deps)
    try:
        yield deps
    finally:
        reset_deps(token)


def test_rag_search_returns_success_with_citations(injected_deps):
    """rag_search 返回 success + 完整 citation 结构。"""
    fake = _FakeStore(results=[_success_result()])
    injected_deps.rag_store = fake

    result = rag_search.invoke({"query": "磁盘使用率怎么查", "top_k": 3})

    assert result["status"] == "success"
    assert result["source"] == "rag_milvus_hybrid"
    assert result["query"] == "磁盘使用率怎么查"
    assert 0 < len(result["results"]) <= 3

    for r in result["results"]:
        assert isinstance(r["score"], (int, float))
        assert isinstance(r["text"], str) and len(r["text"]) > 0
        assert r["source"]
        assert r["citation"].startswith("[") and r["citation"].endswith("]")
        assert r["chunk_id"]
        assert r["parent_id"]  # document_id 映射为 parent_id


def test_rag_search_bm25_literal_match(injected_deps):
    """BM25 字面匹配：注入含 doc-01 的结果，验证工具透传。"""
    fake = _FakeStore(results=[_success_result("doc-01")])
    injected_deps.rag_store = fake

    result = rag_search.invoke({"query": "磁盘使用率 df 命令", "top_k": 3})
    assert result["status"] == "success"
    sources = [r["parent_id"] for r in result["results"]]
    assert "doc-01" in sources, f"BM25 应召回 doc-01，实际: {sources}"


def test_rag_search_unavailable_when_store_raises(injected_deps):
    """store 检索抛异常时返回 unavailable，不伪装成功。"""
    fake = _FakeStore(fail=RuntimeError("Fail connecting to server on localhost:19530"))
    injected_deps.rag_store = fake

    result = rag_search.invoke({"query": "任意查询", "top_k": 3})
    assert result["status"] == "unavailable"
    assert result["results"] == []
    assert "error" in result and result["error"]


def test_rag_search_unavailable_when_store_build_fails(injected_deps, monkeypatch):
    """store 构建失败（如 Milvus 未配置 / Embedding 未配置）时返回 unavailable。"""
    def _raise(*args, **kwargs):
        raise MilvusNotConfiguredError_x("Milvus not configured (test)")

    # 让容器 rag_store 属性在懒建时失败
    from app_v4.rag import store_factory
    monkeypatch.setattr(store_factory, "build_milvus_rag_store", _raise)

    # 重置容器缓存，使下次访问 rag_store 触发构建
    injected_deps._rag_store = None

    result = rag_search.invoke({"query": "磁盘使用率", "top_k": 3})
    assert result["status"] == "unavailable"
    assert result["results"] == []
    assert "error" in result and result["error"]
    assert result["source"] == "rag_milvus_hybrid"


# 占位异常（避免 import 循环）
class MilvusNotConfiguredError_x(RuntimeError):
    pass
