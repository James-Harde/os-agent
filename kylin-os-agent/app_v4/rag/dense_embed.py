"""稠密嵌入 test double — FakeDenseEmbedder。

生产级稠密路通过 real_embed.py 的 OpenAICompatibleEmbedder（基于 langchain-openai，
独立 Embedding 配置）接入 Milvus。本模块仅保留 FakeDenseEmbedder 作为测试 double：
确定性、离线、单位向量，用于注入 MilvusRAGStore 做离线/集成测试，不访问 Embedding API。

FakeDenseEmbedder 实现 ``langchain_core.embeddings.Embeddings`` 接口，
可直接作为官方 langchain-milvus ``Milvus`` 向量存储的 ``embedding_function``。

诚实限制：
  - 这是 test double，仅用于让离线测试/学习基线能跑通结构，**不能作为检索质量证据**。
  - embed 产出的向量与语义无关（纯随机），不可用于衡量召回/排序效果。
  - 跨进程确定性：使用 hashlib 摘要作为种子，避免 Python hash() 受 PYTHONHASHSEED
    随机化影响（hash() 在每次启动时加盐，导致跨进程结果不一致）。
"""

from __future__ import annotations

import logging

import numpy as np
from langchain_core.embeddings import Embeddings

logger = logging.getLogger("app_v4.rag.dense_embed")


class FakeDenseEmbedder(Embeddings):
    """确定性 fake dense embedding（test double，实现 ``Embeddings`` 接口）。

    注意：dim 是 read-only property（返回 _dim），构造时传入 dim 参数即可。
    """

    def __init__(self, dim: int = 8, seed: int = 0) -> None:
        super().__init__()
        self._dim = dim
        self.seed = seed
        self._fitted = True

    def fit(self, texts) -> "FakeDenseEmbedder":
        return self

    def _text_seed(self, text: str) -> int:
        """从文本生成稳定种子（跨进程、跨 PYTHONHASHSEED 一致）。"""
        import hashlib

        # sha256 → 取前 8 字节 → 无符号 int → 加上实例 seed → 规约到 [0, 2**32)
        digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
        value = int.from_bytes(digest, byteorder="little", signed=False)
        return (value + self.seed) % (2**32)

    # ------------------------------------------------------------------
    # Embeddings 接口（官方 Milvus 存储所需）
    # ------------------------------------------------------------------
    def embed_query(self, text: str) -> list[float]:
        return self.encode(text).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.encode_batch(texts).tolist()

    # ------------------------------------------------------------------
    # 兼容接口（返回 numpy 数组）
    # ------------------------------------------------------------------
    def encode(self, text: str) -> np.ndarray:
        seed = self._text_seed(text)
        rng = np.random.RandomState(seed)
        vec = rng.randn(self._dim).astype(np.float32)
        norm = np.linalg.norm(vec) or 1.0
        return vec / norm

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        return np.array([self.encode(t) for t in texts])

    @property
    def dim(self) -> int:
        return self._dim
