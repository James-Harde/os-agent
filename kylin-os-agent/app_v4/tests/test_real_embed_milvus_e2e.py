"""真实 Embedding + Milvus 端到端 E2E — 独立 marker，不纳入默认离线套件。

验证完整 RAG 生产链路：
  真实 Ollama Embedding（OpenAICompatibleEmbedder）
  → 独立临时 Milvus collection（uuid 命名，隔离）
  → 写入（ingest：切片 + 嵌入 + 写入 + flush）
  → 混合检索（dense + BM25 + RRF）
  → 校验 source / document_id / chunk_id / citation
  → finally 清理 collection（drop_collection，即使测试失败也执行）

运行方式：
    pytest -m real_embedding -v -s

默认 `pytest` 运行会跳过本文件（pytest.ini addopts 排除 real_embedding）。
仅当 EMBEDDING_* 三项全部配置 且 Milvus Standalone 可达时才真正执行；
否则明确 skip，不使用 Fake / SVD / mock 冒充成功。

安全：日志只记录 provider / model / 维度 / 耗时，绝不输出 api_key。
"""

from __future__ import annotations

import time
import uuid

import pytest
from langchain_core.documents import Document

from app_v4.rag.real_embed import OpenAICompatibleEmbedder

# 整个文件标记为 real_embedding，默认离线套件排除
pytestmark = pytest.mark.real_embedding


def _embedding_configured() -> bool:
    from app_v4.settings import load_settings
    return load_settings().embedding_configured


def _milvus_available() -> bool:
    """探测本地 Milvus Standalone 是否可达。"""
    try:
        from pymilvus import MilvusClient
        client = MilvusClient(uri="http://127.0.0.1:19530", timeout=3, server_name="milvus")
        client.list_collections()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def _real_embedder_dim() -> tuple[OpenAICompatibleEmbedder, int]:
    """构造真实 embedder 并探测维度（MilvusRAGStore 构造需要维度）。"""
    from app_v4.settings import load_settings

    s = load_settings()
    if not s.embedding_configured:
        pytest.skip("EMBEDDING_* 未完整配置，跳过真实 Embedding+Milvus E2E")
    if not _milvus_available():
        pytest.skip("Milvus Standalone 不可达（需 Docker），跳过真实 Embedding+Milvus E2E")

    embedder = OpenAICompatibleEmbedder(
        base_url=s.embedding_base_url,
        api_key=s.embedding_api_key,
        model=s.embedding_model,
        dimensions=s.embedding_dimensions,
        timeout=s.embedding_timeout,
    )
    # 探测真实维度：一次小 embed 确定向量长度。
    probe = embedder.embed_query("维度探测")
    dim = len(probe)
    assert 64 <= dim <= 8192, f"真实 embedding 维度异常: {dim}"
    return embedder, dim


def _corpus() -> list[Document]:
    """小型隔离语料（document_id 与 integration 测试相同，便于交叉验证 BM25 区分度）。"""
    return [
        Document(
            page_content="磁盘使用率通过 df -h 命令查看，显示每个分区已用和可用空间。",
            metadata={"source": "faq-disk", "document_id": "doc-01"},
        ),
        Document(
            page_content="系统日志通过 journalctl 查看，支持按级别过滤。",
            metadata={"source": "faq-log", "document_id": "doc-02"},
        ),
        Document(
            page_content="进程占用通过 top 或 ps aux 查看。",
            metadata={"source": "faq-proc", "document_id": "doc-03"},
        ),
    ]


def test_real_embedding_to_milvus_e2e(_real_embedder_dim, capsys):
    """端到端：真实 Embedding → 临时 Milvus collection → 写入 → 混合检索 → citation → 清理。"""
    from app_v4.rag.milvus_store import MilvusRAGStore

    embedder, dim = _real_embedder_dim
    # 独立临时 collection（uuid 命名），避免与生产 / 集成测试互相污染。
    col = f"e2e_embed_{uuid.uuid4().hex[:8]}"
    store = MilvusRAGStore(
        connection_args={"uri": "http://127.0.0.1:19530", "timeout": 10},
        embedder=embedder,
        corpus_documents=[],
        dimension=dim,
        collection_name=col,
    )
    # collection 在 MilvusRAGStore 构造时即已创建（含 schema）。
    # 因此无论 ingest 成功还是中途失败，finally 都必须查询并删除该临时 collection。
    created = True
    try:
        # ---- 写入（真实 embedding + 真实 Milvus 写入 + flush）----
        t0 = time.perf_counter()
        n = store.ingest(_corpus())
        ingest_elapsed = time.perf_counter() - t0
        assert n > 0, "ingest 应写入至少 1 个 chunk"

        # 写入后可见
        count = store.document_count()
        assert count == n, f"document_count 应等于写入数 {n}, 实际 {count}"

        # ---- 混合检索（dense + BM25 + RRF）----
        t1 = time.perf_counter()
        results = store.search("磁盘 df 命令", top_k=3)
        search_elapsed = time.perf_counter() - t1
        assert len(results) >= 1, "混合检索应返回至少 1 条结果"

        # ---- 校验 citation 结构 ----
        for r in results:
            assert set(r.keys()) == {"score", "text", "source", "document_id", "chunk_id", "citation"}, \
                f"结果结构不完整: {r.keys()}"
            assert r["text"], "结果 text 非空"
            assert r["source"], "结果 source 非空"
            assert r["document_id"], "结果 document_id 非空"
            assert r["chunk_id"], "结果 chunk_id 非空"
            assert r["citation"].startswith("[") and r["citation"].endswith("]"), \
                f"citation 格式应为 [...], 实际 {r['citation']}"
            assert isinstance(r["score"], float)

        # BM25 区分度：中文字面匹配 "磁盘" → top-1 应为 doc-01。
        assert results[0]["document_id"] == "doc-01", (
            f"BM25 字面匹配应把 doc-01 排在 top-1, 实际 top-1 为 {results[0]['document_id']}"
        )

        with capsys.disabled():
            print(
                f"\n[real_embed_milvus_e2e] OK model={embedder.model} dim={dim} "
                f"chunks={n} top1={results[0]['document_id']} "
                f"ingest={ingest_elapsed:.2f}s search={search_elapsed:.2f}s"
            )
    finally:
        # ---- 清理：无论测试成功与否，必须删除临时 collection ----
        if created:
            try:
                store._ensure_store()
                if store._store.client.has_collection(col):
                    store._store.client.drop_collection(col)
                    # 验证已删除
                    assert not store._store.client.has_collection(col), \
                        f"临时 collection {col} 应已删除"
            except Exception as exc:
                # 清理失败不应冒充通过，但也不应掩盖原始测试结果。
                pytest.fail(f"临时 Milvus collection {col} 清理失败: {exc}")
