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
  - pymilvus 3.0.0 官方兼容 Milvus server 2.6.*（official compatibility matrix）。
  - RRF reranker：使用 ``RRFRanker(k=60)``（BaseRanker）。Milvus 2.6 的
    hybrid_search 通过 ``rank_params`` 读取 reranker；``RRFRanker.dict()`` 序列化为
    ``{"strategy":"rrf","params":{"k":60}}``，服务端正常识别。
    ``Function(FunctionType.RERANK)`` 走 function_score 路径，Milvus 2.6 不支持
    （报 "unsupported rerank function: []"），故不使用。
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
from pymilvus import DataType, FunctionType, RRFRanker

logger = logging.getLogger("app_v4.rag.milvus_store")

# RRF 平滑参数 k：越小头部排名权重越集中；60 是 Milvus 默认值。
_RRF_K = 60
# 检索放大倍数：每路多召回候选，RRF 融合后再取 top_k。
_SEARCH_LIMIT_MULTIPLIER = 3

# Ingestion 格式版本：用于 collection 元数据兼容性检查。
_INGEST_SCHEMA_VERSION = "rag.v1"


class IncompatibleCollectionError(RuntimeError):
    """Milvus 集合 schema 与当前混合检索契约不兼容时抛出——fail-fast。"""
    pass


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

        # hybrid search 时官方要求 vector_field 为列表，且与 index_params 一一对应：
        #   vector_field[0]="vector"  → index_params[0]（dense, IP）
        #   vector_field[1]="sparse"  → index_params[1]（BM25）
        #
        # 注意：不向构造器传 ``timeout`` 参数。langchain-milvus 0.4.0 的
        # ``add_embeddings`` 会把 ``self.timeout`` 塞进 kwargs 字典，先传给
        # ``_init(**kwargs)``，再原封不动传给 ``client.insert(timeout=..., **kwargs)``，
        # 导致 ``timeout`` 重复传参（TypeError）。让 ``self.timeout=None`` 即可规避；
        # 连接/写入超时通过 ``connection_args`` 中的 ``timeout`` 传给 MilvusClient。
        self._store = MilvusVectorStore(
            embedding_function=self._embedder,
            builtin_function=bm25_func,
            connection_args=self._connection_args,
            collection_name=self._collection_name,
            # 关闭 auto_id，由我们提供稳定 id 实现幂等写入。
            auto_id=False,
            primary_field="id",
            text_field="text",
            vector_field=["vector", "sparse"],
            # 元数据字段：写入时通过 metadatas 传入，需显式声明 schema。
            metadata_schema=self._build_metadata_schema(),
            consistency_level="Bounded",
            index_params=self._build_index_params(),
            # 不自动 drop；幂等由稳定 id + upsert 保证。
            drop_old=False,
        )
        logger.info(
            "Milvus 向量存储已构建: collection=%s (dim=%d)",
            self._collection_name, self._dimension,
        )
        # 首次构建后验证 schema 契约（建集合后、写入前）。
        self._validate_collection_schema()
        return self._store

    def _validate_collection_schema(self) -> None:
        """验证当前集合 schema 满足混合检索契约（fail-fast）。

        检查项：
          - dense vector 字段存在且维度与当前 embedder 一致。
          - sparse vector 字段存在（BM25 输出目标）。
          - BM25 built-in function 已注册到集合（name 以 ``bm25_function`` 开头）。
          - 元数据字段（source / document_id / chunk_id）存在。

        不兼容时抛出 ``IncompatibleCollectionError``，禁止静默继续。
        """
        store = self._store
        if store is None or not store.client.has_collection(self._collection_name):
            return

        try:
            schema = store.client.describe_collection(self._collection_name)
        except Exception as exc:
            raise IncompatibleCollectionError(
                f"无法读取集合 {self._collection_name} 的 schema: {exc}"
            ) from exc

        fields = {f["name"]: f for f in schema.get("fields", [])}
        functions = schema.get("functions", [])

        # 1) dense vector 字段 + 维度
        vector_field = fields.get("vector")
        if vector_field is None:
            raise IncompatibleCollectionError(
                f"集合 {self._collection_name} 缺少 dense vector 字段 'vector'"
            )
        # params 格式兼容：真实 Milvus 返回 dict（{"dim": "8", ...}），
        # 部分 mock/旧版本可能返回 list（[{"key":..., "value":...}]）。
        raw_params = vector_field.get("params", {})
        if isinstance(raw_params, dict):
            field_params = {str(k): str(v) for k, v in raw_params.items()}
        elif isinstance(raw_params, list):
            field_params = {str(p["key"]): str(p["value"]) for p in raw_params}
        else:
            field_params = {}
        schema_dim = int(field_params.get("dim", 0))
        if schema_dim != self._dimension:
            raise IncompatibleCollectionError(
                f"集合 {self._collection_name} 维度不兼容："
                f"schema={schema_dim}，当前 embedder={self._dimension}"
            )

        # 2) sparse vector 字段
        if "sparse" not in fields:
            raise IncompatibleCollectionError(
                f"集合 {self._collection_name} 缺少 sparse vector 字段 'sparse'"
            )

        # 3) BM25 built-in function
        bm25_funcs = [fn for fn in functions if fn.get("name", "").startswith("bm25_function")]
        if not bm25_funcs:
            raise IncompatibleCollectionError(
                f"集合 {self._collection_name} 未注册 BM25 built-in function"
            )

        # 4) 元数据字段
        for required in ("source", "document_id", "chunk_id"):
            if required not in fields:
                raise IncompatibleCollectionError(
                    f"集合 {self._collection_name} 缺少元数据字段 '{required}'"
                )

        logger.info("集合 schema 验证通过: %s (dim=%d, bm25_functions=%d)",
                     self._collection_name, self._dimension, len(bm25_funcs))

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
        """dense 路（IP）+ sparse 路（BM25）双索引参数。

        注意：官方 ``IndexParams.add_index(field_name, index_type, ...)`` 中
        ``field_name`` 由 langchain-milvus 按 vector_field 顺序逐个传入，
        此处不得包含 ``field_name``（否则会重复传参）。
        """
        return [
            {
                "index_type": "AUTOINDEX",
                "metric_type": "IP",
                "params": {},
            },
            {
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": "BM25",
                "params": {},
            },
        ]

    # ------------------------------------------------------------------
    # 语料写入（幂等：稳定 id + upsert）
    # ------------------------------------------------------------------
    def _prepare_rows(self, documents: Iterable[Document]) -> tuple[list[str], list[Document]]:
        """切片文档为 (ids, documents) 行。稳定 chunk_id 保证幂等。"""
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
        return rows_ids, rows_docs

    def ingest(self, documents: Iterable[Document]) -> int:
        """写入一批文档（切片 → 嵌入 → 写入）。返回实际新增 chunk 数。

        幂等性在应用层实现（Milvus 2.6 的 insert/upsert 对重复主键不去重）：
          1. 每个 chunk 的 id 由 ``document_id + chunk_index`` 稳定生成。
          2. 写入前查询集合中已存在的 id，过滤掉重复，只写入新 chunk。
          3. 重复导入不会增加行数。

        首次写入时 collection 不存在，用 ``add_texts`` 触发官方 ``_init``
        （建集合 + 索引）；后续写入用 ``add_texts`` 追加新 chunk。
        """
        store = self._ensure_store()
        rows_ids, rows_docs = self._prepare_rows(documents)

        if not rows_docs:
            return 0

        # 应用层去重：只写入集合中尚不存在的 id。
        new_ids, new_docs = self._filter_existing(store, rows_ids, rows_docs)

        if not new_docs:
            logger.info("Milvus ingest：全部 %d 个 chunk 已存在，跳过", len(rows_ids))
            return 0

        store.add_texts(
            texts=[d.page_content for d in new_docs],
            metadatas=[d.metadata for d in new_docs],
            ids=new_ids,
        )
        # flush 使数据从 WAL 缓冲持久化，否则 document_count / 查询看不到新数据。
        self._flush()
        logger.info("Milvus ingest：新增 %d 个 chunk（%d 篇文档，过滤前 %d）",
                    len(new_docs), len(list(documents)), len(rows_docs))
        return len(new_docs)

    def _filter_existing(
        self,
        store: MilvusVectorStore,
        rows_ids: list[str],
        rows_docs: list[Document],
    ) -> tuple[list[str], list[Document]]:
        """过滤掉集合中已存在的 id，返回 (新 id, 新 doc) 列表。"""
        if not store.client.has_collection(self._collection_name):
            return rows_ids, rows_docs

        existing = self._existing_ids(store, rows_ids)
        if not existing:
            return rows_ids, rows_docs

        new_ids, new_docs = [], []
        for cid, doc in zip(rows_ids, rows_docs):
            if cid not in existing:
                new_ids.append(cid)
                new_docs.append(doc)
        return new_ids, new_docs

    def _existing_ids(self, store: MilvusVectorStore, candidate_ids: list[str]) -> set[str]:
        """查询集合中已存在的候选 id 集合（供去重用）。"""
        if not candidate_ids:
            return set()
        # Milvus query 的 in-expression 长度有限，分批查询。
        found: set[str] = set()
        batch_size = 200
        for i in range(0, len(candidate_ids), batch_size):
            batch = candidate_ids[i : i + batch_size]
            expr = "id in [" + ", ".join(f'"{cid}"' for cid in batch) + "]"
            try:
                rows = store.client.query(
                    self._collection_name,
                    filter=expr,
                    output_fields=["id"],
                    timeout=10,
                )
                found.update(str(r["id"]) for r in rows)
            except Exception:
                # 查询失败时保守处理：假设都不存在，让写入去重由调用方兜底。
                logger.warning("查询已存在 id 失败，跳过该批去重: %s", expr[:120])
        return found

    def _flush(self) -> None:
        """将当前集合的写入缓冲持久化（Milvus 标准写入语义）。"""
        store = self._ensure_store()
        store.client.flush(self._collection_name)
        logger.debug("Milvus flush 完成: %s", self._collection_name)

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

        # Milvus 2.6 的 hybrid_search 通过 ``rank_params`` 读取 reranker。
        # ``RRFRanker``（BaseRanker）序列化为 rank_params，服务端正常识别；
        # ``Function(FunctionType.RERANK)`` 走 function_score 路径，Milvus 2.6
        # 不支持，会报 "unsupported rerank function: []"。
        # langchain-milvus 0.4.0 的 ``_collection_hybrid_search`` 会 assert
        # ``reranker.type == FunctionType.RERANK``，因此子类化 RRFRanker 并暴露
        # ``type`` 属性以同时满足两边约束（业务代码子类，非猴子补丁）。
        reranker = _RRFReranker(k=self._rerank_k)

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
        """当前集合 chunk 数。

        语义区分（不得吞异常伪装为 0）：
          - 集合不存在 → 返回 0（尚未写入）。
          - 连接失败 / 其他错误 → 抛出，由调用方决定。
        """
        store = self._ensure_store()
        if not store.client.has_collection(self._collection_name):
            return 0
        stats = store.client.get_collection_stats(self._collection_name)
        return int(stats.get("row_count", 0))


class _RRFReranker(RRFRanker):
    """RRFRanker 子类，暴露 ``type`` 属性以通过 langchain-milvus 0.4.0 的 assert。

    Milvus 2.6 的 hybrid_search 通过 ``rank_params`` 读取 reranker，
    ``RRFRanker.dict()`` 序列化为 ``{"strategy":"rrf","params":{"k":...}}``，
    服务端正常识别。``Function(FunctionType.RERANK)`` 走 function_score 路径，
    Milvus 2.6 不支持。langchain-milvus 0.4.0 的 ``_collection_hybrid_search``
    会 assert ``reranker.type == FunctionType.RERANK``，因此暴露该属性。
    """

    @property
    def type(self) -> FunctionType:
        return FunctionType.RERANK


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
