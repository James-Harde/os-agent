"""rag_search 工具接入 Agent 主链路测试。

覆盖：
  1. rag_search 能被 LLM 规划并调用（通过 /api/chat）
  2. 返回结果包含 score + text + source
  3. 空数据库时安全返回（不抛异常）
"""

import pytest
from fastapi.testclient import TestClient

from app_v4.tools.system_tools import rag_search, _get_rag_index
from app_v4.rag.pipeline import RAGIndex


def test_rag_search_planned_and_called_via_chat(client: TestClient):
    """知识库意图：fake model 应规划 rag_search 并被执行。"""
    # "知识库" 关键词触发 fake model 的 rag_search 规划分支
    resp = client.post("/api/chat", json={"message": "查询知识库：磁盘使用率怎么查"})
    assert resp.status_code == 200
    data = resp.json()

    tool_names = [c["tool_name"] for c in data.get("tool_calls", [])]
    assert "rag_search" in tool_names, f"应调用 rag_search，实际调用：{tool_names}"

    rag_call = next(c for c in data["tool_calls"] if c["tool_name"] == "rag_search")
    assert rag_call["status"] == "success"
    assert "data" in rag_call


def test_rag_search_result_contains_score_text_source(client: TestClient):
    """rag_search 返回结构必须包含 score + text + source。"""
    resp = client.post("/api/chat", json={"message": "知识库 FAQ 端口查询"})
    assert resp.status_code == 200
    data = resp.json()

    rag_call = next(
        (c for c in data.get("tool_calls", []) if c["tool_name"] == "rag_search"),
        None,
    )
    assert rag_call is not None, "应调用 rag_search"

    results = rag_call["data"].get("results", [])
    assert len(results) > 0, "知识库命中 FAQ，不应为空"

    first = results[0]
    # 必须含 score / text / source
    assert "score" in first
    assert "text" in first
    assert "source" in first
    # 类型检查
    assert isinstance(first["score"], (int, float))
    assert isinstance(first["text"], str) and len(first["text"]) > 0
    # 知识库应有真实命中（非空字符串）
    assert first["source"]


def test_rag_search_empty_database_safe(monkeypatch):
    """空数据库时 rag_search 安全返回（不抛异常）。"""
    # 替换为空索引
    empty = RAGIndex()
    empty.fit()  # 空 fit，标记 _fitted=True 但 chunks 为空
    monkeypatch.setattr("app_v4.tools.system_tools._rag_index", empty)

    result = rag_search.invoke({"query": "任意查询", "top_k": 3})

    assert result["status"] == "success"
    assert result["results"] == []  # 空库返回空列表，不抛异常


def test_rag_search_registered_as_auto():
    """rag_search 应注册为 auto 权限（确保 plan_node 能暴露给 LLM）。"""
    from app_v4.tools.registry import get_tool_permission, get_auto_tool_names
    assert get_tool_permission("rag_search") == "auto"
    assert "rag_search" in get_auto_tool_names()


def test_rag_search_direct_invoke():
    """直接调用 rag_search 工具：命中查询返回 top_k 条结果。"""
    # 强制重建索引（避免空索引 monkeypatch 影响）
    idx = _get_rag_index()
    assert len(idx) > 0, "知识库索引应非空"

    result = rag_search.invoke({"query": "磁盘使用率", "top_k": 3})
    assert result["status"] == "success"
    assert result["source"] == "rag_index"
    assert result["query"] == "磁盘使用率"
    assert len(result["results"]) <= 3
    assert len(result["results"]) > 0
    # 命中"磁盘"相关（SAMPLE_DATASET 第一条就是磁盘 FAQ）
    assert any("磁盘" in r["text"] or "df" in r["text"] for r in result["results"])
