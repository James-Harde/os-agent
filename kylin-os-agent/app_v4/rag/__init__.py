"""RAG 子包 — Milvus 混合检索主路径。

模块：
  - milvus_store    : MilvusRAGStore（dense + BM25 sparse + RRF + citations）
  - store_factory   : build_milvus_rag_store 工厂（配置 + 语料 + Embedding + Milvus 装配）
  - real_embed      : OpenAICompatibleEmbedder（真实 Embedding 后端，独立配置）
  - dense_embed     : FakeDenseEmbedder（测试 double，离线确定性）
  - metrics         : Recall@k / MRR@k / nDCG@k 评测指标（通用，不绑定检索后端）
  - data/           : 版本化语料（corpus_v1.json）

主路径唯一：rag_search → MilvusRAGStore.search → Milvus hybrid_search。
"""

from app_v4.rag.milvus_store import MilvusRAGStore

__all__ = ["MilvusRAGStore"]
