"""Milvus RAG 存储工厂 — 把配置、语料、Embedding、Milvus 客户端装配成 MilvusRAGStore。

职责边界：
  - 从 settings 读取 Milvus / Embedding 配置（两者独立）。
  - 加载版本化语料（corpus_v1.json）为 LangChain Document。
  - 构建真实 Embedding 后端（real_embed.OpenAICompatibleEmbedder）；
    未配置时 fail-fast（EmbeddingNotConfiguredError），禁止静默回退。
  - 连接 Milvus：必须显式配置 MILVUS_URI（指向 Docker Milvus Standalone）。
    未配置时 fail-fast（MilvusNotConfiguredError），禁止回退到嵌入式 Milvus Lite。
  - 探测 embedding 维度（一次 probe encode），用于建集合。

调用方（通过依赖注入容器）捕获所有异常 → 在 rag_search 工具边界转为结构化 unavailable。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from app_v4.rag.milvus_store import MilvusRAGStore

logger = logging.getLogger("app_v4.rag.store_factory")

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_COLLECTION = "rag_knowledge"


class MilvusNotConfiguredError(RuntimeError):
    """Milvus 未显式配置（MILVUS_URI 为空）时抛出——禁止静默回退到 Lite。"""
    pass


def _load_corpus_documents(
    corpus_version: str = "corpus_v1",
) -> list[Document]:
    """加载版本化语料为 LangChain Document（保留 id/source 元数据）。"""
    path = DATA_DIR / f"{corpus_version}.json"
    with open(path, encoding="utf-8") as f:
        corpus = json.load(f)
    docs: list[Document] = []
    for doc in corpus["documents"]:
        docs.append(Document(
            page_content=doc["text"],
            metadata={"source": doc.get("source", doc["id"]), "document_id": doc["id"]},
        ))
    logger.info("加载语料：%d 篇文档（%s）", len(docs), corpus_version)
    return docs


def _build_embedder(settings: Any) -> Any:
    """构建真实 Embedding 后端。未配置则 fail-fast。"""
    from app_v4.rag.real_embed import build_embedder_from_settings

    return build_embedder_from_settings(settings)


def _probe_dimension(embedder: Any) -> int:
    """通过一次 probe encode 确定 embedding 维度（用于建 Milvus 集合）。"""
    vec = embedder.embed_query("dimension probe")
    dim = int(len(vec))
    logger.info("Embedding 维度探测：%d", dim)
    return dim


def _resolve_connection_args(settings: Any) -> dict[str, Any]:
    """解析 Milvus 连接参数：必须显式配置 MILVUS_URI，否则 fail-fast。

    禁止回退到嵌入式 Milvus Lite：
      - milvus-lite 在 Windows 上不被官方支持（langchain-milvus 0.4.0 将其限制为
        ``sys_platform != "win32"``）；
      - 生产路径必须指向 Docker Milvus Standalone。
    """
    if not settings.milvus_uri:
        raise MilvusNotConfiguredError(
            "Milvus 未配置：MILVUS_URI 为空。请显式配置指向 Docker Milvus Standalone 的 URI"
            "（如 http://127.0.0.1:19530）。禁止回退到嵌入式 Milvus Lite。"
        )
    return {"uri": settings.milvus_uri, "timeout": settings.milvus_timeout}


def build_milvus_rag_store(
    settings: Any | None = None,
) -> MilvusRAGStore:
    """装配 MilvusRAGStore（主入口）。

    异常语义：任何环节失败（语料缺失 / Embedding 未配置 / Milvus 未配置 / Milvus 不可达）
    均直接抛出，由调用方在工具边界转为 unavailable，绝不在此处静默回退。
    """
    from app_v4.settings import load_settings

    if settings is None:
        settings = load_settings()

    corpus_docs = _load_corpus_documents()
    embedder = _build_embedder(settings)
    dimension = _probe_dimension(embedder)
    connection_args = _resolve_connection_args(settings)

    store = MilvusRAGStore(
        connection_args=connection_args,
        embedder=embedder,
        corpus_documents=corpus_docs,
        dimension=dimension,
        collection_name=settings.milvus_collection or DEFAULT_COLLECTION,
    )
    logger.info("MilvusRAGStore 装配完成（collection=%s）", store._collection_name)
    return store
