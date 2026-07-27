"""MilvusRAGStore 聚焦测试（官方 langchain-milvus 集成）。

分层（诚实区分）：
  - unit：mock 官方 ``MilvusVectorStore``，验证 ingest 数据流 / 幂等 ID / search
    结果映射 / citation 结构 / 语料懒加载。不触网，不访问 Embedding API。
    注意：unit 仅证明业务边界（分片、ID 生成、结果映射），不证明检索质量。
  - integration：真实 Milvus Standalone（Docker，v2.6+），验证 dense + BM25 + RRF
    端到端、中文关键词区分度、重复导入不翻倍。需 Docker；无 Docker 时跳过。
  - smoke：真实 Embedding + 真实 Milvus。需 Docker + Embedding 凭据。

无 Docker 时 integration/smoke 标记为 skip（或 xfail），不计为真实验收通过。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from app_v4.rag.dense_embed import FakeDenseEmbedder
from app_v4.rag.milvus_store import MilvusRAGStore


# ---------------------------------------------------------------------------
# 共享语料
# ---------------------------------------------------------------------------
def _corpus() -> list[Document]:
    return [
        Document(page_content="磁盘使用率通过 df -h 命令查看，显示每个分区已用可用空间。",
                 metadata={"source": "faq-disk", "document_id": "doc-01"}),
        Document(page_content="系统日志通过 journalctl 查看，支持按级别过滤。",
                 metadata={"source": "faq-log", "document_id": "doc-02"}),
        Document(page_content="进程占用通过 top 或 ps aux 查看。",
                 metadata={"source": "faq-proc", "document_id": "doc-03"}),
    ]


# ===========================================================================
# unit — mock 官方 MilvusVectorStore
# ===========================================================================
class _FakeVectorStore:
    """模拟官方 MilvusVectorStore 的最小接口（ingest/search 路径）。"""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.collection_name = kwargs.get("collection_name", "rag_knowledge")
        self.upsert_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self._row_count = 0

    def upsert(self, ids: list[str], documents: list[Document], **kwargs: Any) -> None:
        self.upsert_calls.append({"ids": ids, "documents": documents})
        # 模拟 upsert 语义：重复 id 覆盖而非新增（按 id 去重计数）
        existing = {r for r in self._existing_ids}
        new_ids = [i for i in ids if i not in existing]
        self._existing_ids.extend(new_ids)
        self._row_count = len(self._existing_ids)

    _existing_ids: list[str] = []

    def similarity_search_with_score(self, query: str, **kwargs: Any) -> list[tuple[Document, float]]:
        self.search_calls.append({"query": query, **kwargs})
        # 返回一条带完整元数据的命中，验证结果映射
        return [(
            Document(
                page_content="磁盘使用率通过 df -h 命令查看。",
                metadata={"source": "faq-disk", "document_id": "doc-01", "chunk_id": "doc-01-c0"},
            ),
            0.95,
        )]


def _make_store(corpus: list[Document] | None = None, dim: int = 16) -> MilvusRAGStore:
    """构造 store，其内部官方 MilvusVectorStore 尚未构建（懒加载）。"""
    return MilvusRAGStore(
        connection_args={"uri": "http://mock:19530"},
        embedder=FakeDenseEmbedder(dim=dim),
        corpus_documents=corpus if corpus is not None else [],
        dimension=dim,
        collection_name="rag_knowledge",
    )


def _patch_store(mock_store: _FakeVectorStore):
    """patch MilvusVectorStore 类为 mock 实例的工厂。"""
    return patch(
        "app_v4.rag.milvus_store.MilvusVectorStore",
        return_value=mock_store,
    )


def test_ingest_stable_chunk_ids_and_metadata():
    """ingest 应生成稳定 chunk_id（doc-XX-cN）并保留 source/document_id 元数据。"""
    mock = _FakeVectorStore()
    store = _make_store(corpus=[])
    with _patch_store(mock):
        n = store.ingest(_corpus())

    assert n == 3, f"3 篇文档各切 1 个 chunk，应返回 3，实际 {n}"
    assert len(mock.upsert_calls) == 1
    ids = mock.upsert_calls[0]["ids"]
    docs = mock.upsert_calls[0]["documents"]
    assert ids == ["doc-01-c0", "doc-02-c0", "doc-03-c0"], f"chunk id 应稳定: {ids}"
    assert all("-c" in d.metadata["chunk_id"] for d in docs)
    assert [d.metadata["document_id"] for d in docs] == ["doc-01", "doc-02", "doc-03"]
    assert [d.metadata["source"] for d in docs] == ["faq-disk", "faq-log", "faq-proc"]


def test_ingest_idempotent_no_duplicate_ids():
    """同一语料导入两次：upsert 使用稳定 id，第二次覆盖而非新增。

    注意：此处验证 store 层生成相同 id 且调用 upsert（幂等写入）；
    真正的去重由 Milvus upsert 语义保证（mock 模拟该语义）。
    """
    mock = _FakeVectorStore()
    store = _make_store(corpus=[])
    with _patch_store(mock):
        n1 = store.ingest(_corpus())
        n2 = store.ingest(_corpus())

    assert n1 == n2 == 3
    # 两次 ingest 生成的 id 集合相同（幂等关键：稳定 ID）
    ids1 = mock.upsert_calls[0]["ids"]
    ids2 = mock.upsert_calls[1]["ids"]
    assert ids1 == ids2, "重复导入应生成相同 chunk id"
    # mock upsert 按 id 去重 → row_count 保持 3 而非翻倍到 6
    assert mock._row_count == 3, f"重复导入不应翻倍，实际 row_count={mock._row_count}"


def test_search_returns_citation_structure():
    """search 返回结果必须含 score/text/source/document_id/chunk_id/citation。"""
    mock = _FakeVectorStore()
    store = _make_store(corpus=[])
    with _patch_store(mock):
        results = store.search("磁盘使用率", top_k=1)

    assert len(results) == 1
    r = results[0]
    assert set(r.keys()) == {"score", "text", "source", "document_id", "chunk_id", "citation"}
    assert r["document_id"] == "doc-01"
    assert r["chunk_id"] == "doc-01-c0"
    assert r["citation"] == "[doc-01]"
    assert r["source"] == "faq-disk"
    assert isinstance(r["score"], float)

    # 验证官方存储被调用，且 reranker 为 RRF（Function, RERANK 类型）
    assert len(mock.search_calls) == 1
    call = mock.search_calls[0]
    reranker = call.get("reranker")
    assert reranker is not None, "hybrid search 必须传入 reranker"
    assert reranker.type == 3, f"reranker.type 应为 RERANK(3)，实际 {reranker.type}"
    assert reranker.params.get("k") == 60, f"RRF k 应为 60，实际 {reranker.params}"


def test_search_empty_corpus_returns_empty():
    """语料为空时 ingest 返回 0；search 依赖存储返回空。"""
    mock = _FakeVectorStore()
    store = _make_store(corpus=[])
    with _patch_store(mock):
        assert store.ingest([]) == 0
        # 清空模拟搜索结果
        mock.similarity_search_with_score = lambda query, **kw: []
        results = store.search("任意查询", top_k=3)
    assert results == []


def test_corpus_auto_loaded_on_first_search():
    """配置语料后，首次 search 时语料自动懒加载（无需显式 ingest）。"""
    mock = _FakeVectorStore()
    store = _make_store(corpus=_corpus())
    assert mock._row_count == 0
    with _patch_store(mock):
        _ = store.search("磁盘", top_k=1)
    # 首次 search 触发语料写入
    assert len(mock.upsert_calls) == 1, "首次 search 应自动写入语料"
    assert mock._row_count == 3


def test_available_reflects_store_reachability():
    """available 在 store 可达时 True。"""
    mock = _FakeVectorStore()
    mock_client = MagicMock()
    mock_client.list_collections.return_value = ["rag_knowledge"]
    mock.client = mock_client
    store = _make_store()
    with _patch_store(mock):
        assert store.available is True


def test_dimension_must_be_positive():
    """dimension <= 0 时 fail-fast（ValueError）。"""
    with pytest.raises(ValueError):
        MilvusRAGStore(
            connection_args={"uri": "http://mock:19530"},
            embedder=FakeDenseEmbedder(dim=8),
            corpus_documents=[],
            dimension=0,
        )


# ===========================================================================
# integration — 真实 Milvus Standalone（Docker，v2.6+）
# ===========================================================================
def _milvus_available() -> bool:
    """探测本地 Milvus Standalone 是否可达。"""
    try:
        from pymilvus import MilvusClient
        client = MilvusClient(uri="http://127.0.0.1:19530", timeout=3, server_name="milvus")
        client.list_collections()
        return True
    except Exception:
        return False


_MILVUS_AVAILABLE = _milvus_available()


@pytest.mark.integration
@pytest.mark.skipif(not _MILVUS_AVAILABLE, reason="Milvus Standalone 不可达（需 Docker，无 Docker 时跳过）")
class TestIntegrationMilvusStandalone:
    """真实 Milvus Standalone 端到端测试（dense + BM25 + RRF）。"""

    @pytest.fixture()
    def real_store(self, tmp_path: Path) -> MilvusRAGStore:
        # 每个测试用独立 collection 避免互相污染
        import uuid
        col = f"test_rag_{uuid.uuid4().hex[:8]}"
        store = MilvusRAGStore(
            connection_args={"uri": "http://127.0.0.1:19530", "timeout": 10},
            embedder=FakeDenseEmbedder(dim=16),
            corpus_documents=_corpus(),
            dimension=16,
            collection_name=col,
        )
        yield store
        # 清理
        try:
            store._ensure_store()
            if store._store.client.has_collection(col):
                store._store.client.drop_collection(col)
        except Exception:
            pass

    def test_ingest_writes_chunks(self, real_store: MilvusRAGStore):
        real_store.ingest(_corpus())
        assert real_store.document_count() > 0

    def test_search_returns_results_with_citations(self, real_store: MilvusRAGStore):
        results = real_store.search("磁盘使用率", top_k=2)
        assert len(results) == 2
        for r in results:
            assert r["citation"].startswith("[") and r["citation"].endswith("]")
            assert r["document_id"] and r["chunk_id"]
            assert r["text"]

    def test_chinese_keyword_bm25_has_discriminative_power(self, real_store: MilvusRAGStore):
        """中文关键词检索需有区分度：'磁盘' 查询 top-1 必须是 doc-01（字面匹配）。

        不得只断言「位于 top-3」——需断言 BM25 字面匹配把相关文档排在最前。
        """
        results = real_store.search("磁盘 df 命令", top_k=3)
        assert len(results) == 3
        assert results[0]["document_id"] == "doc-01", (
            f"BM25 字面匹配应把 doc-01 排在 top-1，实际 top-1 为 {results[0]['document_id']}"
        )

    def test_ingest_idempotent_real(self, real_store: MilvusRAGStore):
        """同一语料导入两次，chunk 数不翻倍。"""
        real_store.ingest(_corpus())
        count_after_first = real_store.document_count()
        real_store.ingest(_corpus())
        count_after_second = real_store.document_count()
        assert count_after_first == count_after_second, (
            f"重复导入应幂等：首次 {count_after_first}，第二次 {count_after_second}"
        )
