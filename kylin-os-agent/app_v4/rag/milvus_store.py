"""Milvus 混合检索存储 — RAG 生产主路径（官方 langchain-milvus 集成）。

纵向链路（对齐 app4-需求清单 §5）：
  LangChain Document
  → RecursiveCharacterTextSplitter
  → 真实 Embedding（独立配置，langchain-openai 兼容，符合 Embeddings 接口）
  → 官方 langchain-milvus ``Milvus`` 向量存储
  → Milvus Standalone（Docker Compose，v2.6+）
  → Milvus dense retrieval（IP）+ 内置 BM25 sparse retrieval（BM25BuiltInFunction）
  → RRF 融合（Function(FunctionType.RERANK)，Milvus 2.6+ 官方 reranker）
  → source / document_id / chunk_id citation

官方组件依据：
  - langchain-milvus 0.4.0 强制 ``pymilvus>=3.0.0,<4.0``（PyPI requires_dist）。
  - pymilvus 3.0.0 官方兼容矩阵：Milvus 2.6.* ↔ pymilvus 2.6.X；pymilvus 3.0 为同期客户端。
  - RRF reranker：pymilvus 3.0 移除 ``RRFRanker``，改用
    ``Function(FunctionType.RERANK, params={"k": 60})``（langchain-milvus 源码注释
    明确要求 Milvus 2.6+）。
  - BM25：官方推荐 ``BM25BuiltInFunction``（``BM25SparseEmbedding`` 已标记废弃，
    需手动管理语料）。
  - hybrid search：同时提供 ``embedding_function`` + ``builtin_function`` 触发
    ``Milvus._collection_hybrid_search``（官方内置路径，无需废弃的 Retriever）。

诚实约定：
  - 这是唯一默认 RAG 主路径。不引入 cross-encoder rerank、query rewrite、父子索引。
  - 服务不可用时让异常传播到 ``rag_search`` 工具边界，由工具返回结构化 unavailable。
  - ``__init__`` 不触网；集合创建 / 语料写入 / 检索均在首次使用时懒加载。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable, Optional

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_milvus import Milvus as MilvusVectorStore
from langchain_milvus import BM25BuiltInFunction
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pymilvus import DataType, Function, FunctionType

logger = logging.getLogger("app_v4.rag.milvus_store")

# RRF 平滑参数 k：越小头部排名权重越集中；60 是 Milvus 默认值。
_RRF_K = 60
# 检索放大倍数：每路多召回候选，RRF 融合后再取 top_k。
_SEARCH_LIMIT_MULTIPLIER = 3

# Ingestion 格式版本：用于 collection 元数据兼容性检查。
_INGEST_SCHEMA_VERSION = "rag.v1"


class MilvusRAGStore:
    """基于官方 langchain-milvus 的混合检索存储（dense + BM25 sparse + RRF）。

    用法：
        store = MilvusRAGStore(
            connection_args={"uri": "http://127.0.0.1:19530"},
            embedder=OpenAIEmbeddings(...),  # 真实 dense embedder
            corpus_docs=documents,
            dimension=1536,
        )
        store.search("磁盘使用率怎么查", top_k=3)
    """

    def __init__(
        self,
        connection_args: dict[str, Any],
        embedder: Embeddings,
        corpus_documents: list[Document],
        dimension: int,
        collection_name: str = "rag_knowledge",
        chunk_size: int = 400,
        chunk_overlap: int = 40,
        rerank_k: int = _RRF_K,
    ) -> None:
        if dimension <= 0:
            raise ValueError(f"embedding dimension 必须为正整数，收到 {dimension}")
        self._connection_args = connection_args
        self._embedder = embedder
        self._corpus_documents = corpus_documents
        self._dimension = dimension
        self._collection_name = collection_name
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._rerank_k = rerank_k

        self._store: Optional[MilvusVectorStore] = None
        self._ensured = False
        self._ingested = False

    # ------------------------------------------------------------------
    # 懒构建官方 Milvus 向量存储
    # ------------------------------------------------------------------
    def _ensure_store(self) -> MilvusVectorStore:
        """构建（或复用）官方 Milvus 向量存储实例。"""
        if self._store is not None:
            return self._store

        bm25_func = BM25BuiltInFunction(
            input_field_names="text",
            output_field_names="sparse",
            # 中文分词：按 Milvus 官方支持的 analyzer 类型配置。
            # 注意：Milvus 内置中文 analyzer 基于 Jieba；若服务端未启用中文分析器，
            # 会回退到 standard（按空白/标点分词），中文单字仍可被 BM25 命中。
            analyzer_params={"type": "chinese"},
            enable_match=True,
        )

        self._store = MilvusVectorStore(
            embedding_function=self._embedder,
            builtin_function=bm25_func,
            connection_args=self._connection_args,
            collection_name=self._collection_name,
            # 关闭 auto_id，由我们提供稳定 id 实现幂等写入。
            auto_id=False,
            primary_field="id",
            text_field="text",
            vector_field="vector",
            # 元数据字段：写入时通过 metadatas 传入，需显式声明 schema。
            metadata_schema=self._build_metadata_schema(),
            consistency_level="Bounded",
            index_params=self._build_index_params(),
            # 不自动 drop；幂等由稳定 id + upsert 保证。
            drop_old=False,
            timeout=10,
        )
        logger.info(
            "Milvus 向量存储已构建: collection=%s (dim=%d)",
            self._collection_name, self._dimension,
        )
        return self._store

    @staticmethod
    def _build_metadata_schema() -> dict[str, Any]:
        """声明元数据字段 schema（source / document_id / chunk_id）。

        注意：pymilvus 3.0 的 ``schema.add_field`` 要求 dtype 为 ``DataType`` 枚举，
        不接受字符串；max_length 通过 kwargs 传入。
        """
        return {
            "source": {"dtype": DataType.VARCHAR, "max_length": 1024},
            "document_id": {"dtype": DataType.VARCHAR, "max_length": 1024},
            "chunk_id": {"dtype": DataType.VARCHAR, "max_length": 1024},
        }

    @staticmethod
    def _build_index_params() -> list[dict[str, Any]]:
        """dense 路（IP）+ sparse 路（BM25）双索引参数。"""
        return [
            {
                "field_name": "vector",
                "index_type": "AUTOINDEX",
                "metric_type": "IP",
                "params": {},
            },
            {
                "field_name": "sparse",
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": "BM25",
                "params": {},
            },
        ]

    # ------------------------------------------------------------------
    # 语料写入（幂等：稳定 id + upsert）
    # ------------------------------------------------------------------
    def ingest(self, documents: Iterable[Document]) -> int:
        """写入一批文档（切片 → 嵌入 → upsert）。返回实际写入 chunk 数。

        幂等性：每个 chunk 的 id 由 document_id + chunk_index 稳定生成，
        重复导入执行 upsert 而非 insert，不会产生重复 chunk。
        """
        store = self._ensure_store()
        documents = list(documents)

        rows_docs: list[Document] = []
        rows_ids: list[str] = []

        for doc in documents:
            doc_id = str(doc.metadata.get("document_id") or doc.metadata.get("source") or uuid.uuid4().hex)
            chunks = self._splitter.split_documents([doc])
            for j, chunk in enumerate(chunks):
                text = chunk.page_content
                if not text.strip():
                    continue
                chunk_id = f"{doc_id}-c{j}"
                rows_ids.append(chunk_id)
                rows_docs.append(Document(
                    page_content=text,
                    metadata={
                        "source": chunk.metadata.get("source", doc.metadata.get("source", doc_id)),
                        "document_id": doc_id,
                        "chunk_id": chunk_id,
                    },
                ))

        if not rows_docs:
            return 0

        # 官方 upsert：按稳定 id 写入，重复导入覆盖而非新增。
        store.upsert(ids=rows_ids, documents=rows_docs)
        logger.info("Milvus upsert %d 个 chunk（%d 篇文档）", len(rows_docs), len(documents))
        return len(rows_docs)

    def _ensure_corpus_loaded(self) -> None:
        """集合为空时写入语料（幂等，仅首次触发）。"""
        if self._ingested:
            return
        store = self._ensure_store()
        if self.document_count() == 0 and self._corpus_documents:
            self.ingest(self._corpus_documents)
        self._ingested = True

    # ------------------------------------------------------------------
    # 检索（官方 hybrid search：dense + BM25 + RRF）
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """混合检索（dense + BM25 sparse + RRF），返回带 citation 的结果。"""
        store = self._ensure_store()
        self._ensure_corpus_loaded()

        reranker = Function(
            name="rrf",
            function_type=FunctionType.RERANK,
            input_field_names=[],
            params={"k": self._rerank_k},
        )

        # 官方路径：embedding_function + builtin_function → _collection_hybrid_search。
        # reranker 通过 **kwargs 传入，触发 RRF 融合。
        docs_with_scores = store.similarity_search_with_score(
            query=query,
            k=top_k,
            # 每路多召回候选，RRF 融合后再取 top_k。
            fetch_k=top_k * _SEARCH_LIMIT_MULTIPLIER,
            reranker=reranker,
        )

        return [_to_result(doc, score) for doc, score in docs_with_scores]

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """轻量级可达性探测（供工具边界 / 健康检查用）。"""
        try:
            store = self._ensure_store()
            _ = store.client.list_collections()
            return True
        except Exception:
            return False

    def document_count(self) -> int:
        """当前集合 chunk 数（0 表示未写入或集合不存在）。"""
        try:
            store = self._ensure_store()
            stats = store.client.get_collection_stats(store.collection_name)
            return int(stats.get("row_count", 0))
        except Exception:
            return 0


def _to_result(doc: Document, score: float) -> dict[str, Any]:
    """将 (Document, score) 转为统一结果结构（含可核验 citation）。"""
    meta = doc.metadata
    doc_id = meta.get("document_id", "")
    chunk_id = meta.get("chunk_id", "")
    return {
        "score": round(float(score), 4),
        "text": doc.page_content,
        "source": meta.get("source", ""),
        "document_id": doc_id,
        "chunk_id": chunk_id,
        "citation": f"[{doc_id}]",
    }
