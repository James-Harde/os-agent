"""真实 Embedding 后端 — 基于 langchain-openai 的 OpenAI 兼容 embedding。

支持任何兼容 OpenAI embedding API 的服务：
  - OpenAI 官方
  - 本地 Ollama / vLLM
  - 任何 OpenAI 兼容代理

实现 ``langchain_core.embeddings.Embeddings`` 接口，可直接作为官方 langchain-milvus
``Milvus`` 向量存储的 ``embedding_function``（要求 ``Embeddings`` 子类）。

注意：Chat provider 不一定提供 Embedding 服务。Chat 与 Embedding 必须独立显式配置，
禁止根据 Chat 的 base_url/model 猜测 Embedding 模型（曾导致 DeepSeek 404）。
Embedding 未配置时 fail-fast（EmbeddingNotConfiguredError），禁止静默回退 SVD。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from langchain_core.embeddings import Embeddings

logger = logging.getLogger("app_v4.rag.real_embed")


class OpenAICompatibleEmbedder(Embeddings):
    """基于 OpenAI 兼容 API 的真实 embedding（实现 ``Embeddings`` 接口）。

    用法：
        emb = OpenAICompatibleEmbedder(
            base_url="https://api.openai.com/v1",
            api_key="sk-xxx",
            model="text-embedding-3-small",
        )
        vec = emb.embed_query("磁盘满了怎么办")  # list[float]

    实现说明：
        - ``embed_query`` / ``embed_documents`` 委托给 ``langchain_openai.OpenAIEmbeddings``，
          返回 ``list[float]`` / ``list[list[float]]``（符合 ``Embeddings`` 契约）。
        - 保留 ``encode`` / ``encode_batch``（返回 numpy 数组）作为兼容旧调用方的便捷方法。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__()
        self.model = model
        self.dimensions = dimensions
        self._client = None
        self._dim: int | None = None

        # 延迟初始化：构造时不联网，第一次 embed 时才建客户端
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        from langchain_openai import OpenAIEmbeddings

        # check_embedding_ctx_length=False 是关键：
        #   OpenAIEmbeddings 默认会先把文本 tokenize 为 token-ID 数组再发请求。
        #   Ollama 等 OpenAI 兼容 Embedding 服务端期望收到原始字符串，收到 token-ID
        #   数组会返回 HTTP 400 "invalid input type"。设为 False 即直接发送原始文本，
        #   由服务端自行 tokenize。这是官方 LangChain 配置项，无需手写 HTTP。
        self._client = OpenAIEmbeddings(
            base_url=self._base_url,
            api_key=self._api_key,
            model=self.model,
            dimensions=self.dimensions,
            timeout=self._timeout,
            check_embedding_ctx_length=False,
        )
        logger.info("OpenAICompatibleEmbedder 初始化完成: model=%s", self.model)

    def fit(self, texts) -> "OpenAICompatibleEmbedder":
        """API embedding 不需要训练——接口兼容，直接返回 self。"""
        return self

    # ------------------------------------------------------------------
    # Embeddings 接口（官方 Milvus 存储所需）
    # ------------------------------------------------------------------
    def embed_query(self, text: str) -> list[float]:
        self._ensure_client()
        vec = self._client.embed_query(text)
        if self._dim is None:
            self._dim = len(vec)
        return list(vec)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._ensure_client()
        vecs = self._client.embed_documents(texts)
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return [list(v) for v in vecs]

    # ------------------------------------------------------------------
    # 兼容旧接口（返回 numpy 数组）
    # ------------------------------------------------------------------
    def encode(self, text: str) -> np.ndarray:
        return np.array(self.embed_query(text), dtype=np.float32)

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        return np.array(self.embed_documents(texts), dtype=np.float32)

    @property
    def dim(self) -> int:
        if self._dim is not None:
            return self._dim
        return self.dimensions or 1536  # 默认 OpenAI text-embedding-3-small 维度


class EmbeddingNotConfiguredError(RuntimeError):
    """Embedding 未完整配置时抛出——生产路径禁止静默回退 SVD。"""
    pass


def build_embedder_from_settings(
    settings: "Settings | None" = None,
) -> OpenAICompatibleEmbedder:
    """根据独立 Embedding 配置构建真实 embedding 后端。

    要求显式配置 embedding_base_url + embedding_api_key + embedding_model。
    未配置时 fail-fast（抛出 EmbeddingNotConfiguredError），禁止静默回退 SVD。

    参数:
        settings: 显式传入的 Settings 对象（依赖注入，用于单元测试）。
                  为 None 时从 load_settings() 读取真实配置（生产路径）。

    调用方（store_factory / rag_search）应捕获该异常并转为结构化 unavailable，
    而不是伪装检索成功。
    """
    from app_v4.settings import load_settings

    if settings is None:
        settings = load_settings()

    if not settings.embedding_configured:
        raise EmbeddingNotConfiguredError(
            "Embedding 未配置：需要 EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL。"
            "请在 .env 中设置；测试请注入 FakeDenseEmbedder。"
        )

    logger.info(
        "使用真实 API embedding: model=%s, base_url=%s",
        settings.embedding_model, settings.embedding_base_url,
    )
    return OpenAICompatibleEmbedder(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout=settings.embedding_timeout,
    )
