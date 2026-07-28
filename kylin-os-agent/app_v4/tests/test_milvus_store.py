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
class _FakeClient:
    """模拟 MilvusClient 的最小接口（has_collection / get_collection_stats / query / flush）。

    行为对齐真实 Milvus：insert 不去重，已插入的 id 通过 query 可查到。
    """

    def __init__(self, store: "_FakeVectorStore") -> None:
        self._store = store

    def has_collection(self, name: str) -> bool:
        # 集合在首次 add_texts 后才"存在"。
        return len(self._store._existing_ids) > 0

    def get_collection_stats(self, name: str) -> dict[str, Any]:
        return {"row_count": self._store._row_count}

    def query(
        self,
        collection_name: str,
        filter: str | None = None,
        output_fields: list[str] | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        # 返回已插入的 id（生产代码用 id in [...] 过滤，此处简化返回全部）。
        return [{"id": cid} for cid in self._store._existing_ids]

    def flush(self, name: str) -> None:
        pass


class _FakeVectorStore:
    """模拟官方 MilvusVectorStore 的最小接口（ingest/search 路径）。

    重要：``_existing_ids`` 是实例变量（非类变量），避免跨测试污染。
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.collection_name = kwargs.get("collection_name", "rag_knowledge")
        self.add_texts_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self._row_count = 0
        self._existing_ids: list[str] = []  # 实例状态，每个测试独立
        self.client = _FakeClient(self)

    def add_texts(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        self.add_texts_calls.append({"texts": texts, "metadatas": metadatas, "ids": ids})
        # 真实 Milvus insert 不去重；应用层负责去重（生产代码的 _filter_existing）。
        self._existing_ids.extend(ids or [])
        self._row_count = len(self._existing_ids)
        return ids or []

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
    assert len(mock.add_texts_calls) == 1
    ids = mock.add_texts_calls[0]["ids"]
    metas = mock.add_texts_calls[0]["metadatas"]
    assert ids == ["doc-01-c0", "doc-02-c0", "doc-03-c0"], f"chunk id 应稳定: {ids}"
    assert all("-c" in m["chunk_id"] for m in metas)
    assert [m["document_id"] for m in metas] == ["doc-01", "doc-02", "doc-03"]
    assert [m["source"] for m in metas] == ["faq-disk", "faq-log", "faq-proc"]


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

    assert n1 == 3, f"首次 ingest 应新增 3 个 chunk，实际 {n1}"
    # 第二次 ingest：应用层去重（_filter_existing）发现全部已存在，应跳过写入。
    assert n2 == 0, f"重复 ingest 应被应用层去重跳过，实际新增 {n2}"
    # 只发生一次 add_texts（第二次被去重过滤，未写入）。
    assert len(mock.add_texts_calls) == 1, "重复导入不应再次写入"
    # row_count 保持 3 而非翻倍到 6（幂等性在应用层保证）。
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

    # 验证官方存储被调用，且 reranker 为 RRF（RRFRanker, RERANK 类型）
    assert len(mock.search_calls) == 1
    call = mock.search_calls[0]
    reranker = call.get("reranker")
    assert reranker is not None, "hybrid search 必须传入 reranker"
    # _RRFReranker 暴露 .type == RERANK(3) 以通过 langchain-milvus assert；
    # 参数通过 .dict() 访问（pymilvus BaseRanker 标准接口）。
    assert reranker.type == 3, f"reranker.type 应为 RERANK(3)，实际 {reranker.type}"
    assert reranker.dict().get("params", {}).get("k") == 60, (
        f"RRF k 应为 60，实际 {reranker.dict()}"
    )


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
    assert len(mock.add_texts_calls) == 1, "首次 search 应自动写入语料"
    assert mock._row_count == 3


def test_available_reflects_store_reachability():
    """available 在 store 可达时 True。"""
    mock = _FakeVectorStore()
    mock_client = MagicMock()
    mock_client.list_collections.return_value = ["rag_knowledge"]
    # _validate_collection_schema 会调 describe_collection，提供有效结构避免解析错误。
    mock_client.has_collection.return_value = True
    mock_client.describe_collection.return_value = {
        "fields": [
            {"name": "id", "type": 21, "is_primary": True, "params": {}},
            {"name": "text", "type": 21, "params": {}},
            {"name": "vector", "type": 101, "params": {"dim": "16"}},
            {"name": "sparse", "type": 104, "params": {}},
            {"name": "source", "type": 21, "params": {"max_length": "1024"}},
            {"name": "document_id", "type": 21, "params": {"max_length": "1024"}},
            {"name": "chunk_id", "type": 21, "params": {"max_length": "1024"}},
        ],
        "functions": [{"name": "bm25_function_x", "type": 1,
                       "input_field_names": ["text"], "output_field_names": ["sparse"]}],
    }
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


def test_schema_validation_fail_fast_on_missing_bm25():
    """集合缺少 BM25 function 时，schema 验证 fail-fast（IncompatibleCollectionError）。"""
    from app_v4.rag.milvus_store import IncompatibleCollectionError

    mock = _FakeVectorStore()
    # 模拟一个缺少 BM25 function 的集合 schema
    mock.client = MagicMock()
    mock.client.has_collection.return_value = True
    mock.client.describe_collection.return_value = {
        "fields": [
            {"name": "id", "type": 21, "is_primary": True, "params": [{"key": "max_length", "value": "65535"}]},
            {"name": "text", "type": 21, "params": [{"key": "max_length", "value": "65535"}]},
            {"name": "vector", "type": 101, "params": [{"key": "dim", "value": "16"}]},
            {"name": "sparse", "type": 104, "params": []},
            {"name": "source", "type": 21, "params": [{"key": "max_length", "value": "1024"}]},
            {"name": "document_id", "type": 21, "params": [{"key": "max_length", "value": "1024"}]},
            {"name": "chunk_id", "type": 21, "params": [{"key": "max_length", "value": "1024"}]},
        ],
        "functions": [],  # 无 BM25
    }
    store = _make_store()
    with _patch_store(mock):
        with pytest.raises(IncompatibleCollectionError, match="BM25"):
            store._ensure_store()


def test_schema_validation_fail_fast_on_dimension_mismatch():
    """集合维度与 embedder 不匹配时，schema 验证 fail-fast。"""
    from app_v4.rag.milvus_store import IncompatibleCollectionError

    mock = _FakeVectorStore()
    mock.client = MagicMock()
    mock.client.has_collection.return_value = True
    mock.client.describe_collection.return_value = {
        "fields": [
            {"name": "id", "type": 21, "is_primary": True, "params": [{"key": "max_length", "value": "65535"}]},
            {"name": "text", "type": 21, "params": [{"key": "max_length", "value": "65535"}]},
            {"name": "vector", "type": 101, "params": [{"key": "dim", "value": "999"}]},  # 维度不匹配
            {"name": "sparse", "type": 104, "params": []},
            {"name": "source", "type": 21, "params": [{"key": "max_length", "value": "1024"}]},
            {"name": "document_id", "type": 21, "params": [{"key": "max_length", "value": "1024"}]},
            {"name": "chunk_id", "type": 21, "params": [{"key": "max_length", "value": "1024"}]},
        ],
        "functions": [{"name": "bm25_function_abc", "type": 1, "input_field_names": ["text"], "output_field_names": ["sparse"]}],
    }
    store = _make_store(dim=16)
    with _patch_store(mock):
        with pytest.raises(IncompatibleCollectionError, match="维度不兼容"):
            store._ensure_store()


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

    def test_schema_validation_passes_on_compatible_collection(self, real_store: MilvusRAGStore):
        """真实集合 schema 满足混合检索契约时，验证通过且可观察到证据。"""
        real_store.ingest(_corpus())  # 触发建集合
        # 重新构建 store（模拟"再次打开"），触发 schema 验证
        store2 = MilvusRAGStore(
            connection_args={"uri": "http://127.0.0.1:19530", "timeout": 10},
            embedder=FakeDenseEmbedder(dim=16),
            corpus_documents=[],
            dimension=16,
            collection_name=real_store._collection_name,
        )
        store2._ensure_store()  # 不应抛出
        # 可观察到 schema 证据
        schema = store2._store.client.describe_collection(real_store._collection_name)
        functions = schema.get("functions", [])
        assert any(fn["name"].startswith("bm25_function") for fn in functions), (
            f"集合应注册 BM25 function，实际 functions={functions}"
        )
        fields = {f["name"]: f for f in schema.get("fields", [])}
        assert "vector" in fields and "sparse" in fields, "集合应同时有 dense 和 sparse 向量字段"
        # 真实 Milvus 返回 params 为 dict（{"dim": "16", ...}）。
        vector_params = fields["vector"].get("params", {})
        if isinstance(vector_params, dict):
            dim_value = str(vector_params.get("dim", ""))
        else:
            dim_value = str({p["key"]: p["value"] for p in vector_params}.get("dim", ""))
        assert dim_value == "16", f"dense 维度应为 16，实际 params={vector_params}"
