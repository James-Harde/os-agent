"""RAG 流水线 — 索引 + 两阶段检索（向量召回 → BM25 重排）。"""

from __future__ import annotations

from typing import Any

from app_v4.rag.bm25 import BM25
from app_v4.rag.chunk import chunk_text
from app_v4.rag.vectorizer import TfidfVectorizer, cosine, tokenize


class RAGIndex:
    """内存型 RAG 索引。

    用法：
        idx = RAGIndex()
        idx.add_document("如何使用磁盘分析...", source="faq-1")
        results = idx.query("磁盘使用率怎么查", k=3)
    """

    def __init__(self, chunk_size: int = 300, overlap: int = 40) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.chunks: list[dict[str, Any]] = []         # 原始 chunk 记录
        self.tokenized: list[list[str]] = []            # 每个 chunk 的 token
        self.vectorizer = TfidfVectorizer()
        self.bm25 = BM25()
        self._vecs: list[dict[str, float]] = []         # 归一化 TF-IDF 向量
        self._fitted = False

    # ------------------------------------------------------------------
    def add_document(self, text: str, source: str = "") -> int:
        """添加一个文档，自动切片。返回新增 chunk 数。"""
        new_chunks = chunk_text(text, self.chunk_size, self.overlap, source)
        for c in new_chunks:
            self.chunks.append(c)
            self.tokenized.append(tokenize(c["text"]))
        self._fitted = False
        return len(new_chunks)

    def add_chunks(self, texts: list[str], source: str = "") -> None:
        """把一段已切好的文本列表直接作为 chunk 入库（评测常用）。"""
        for t in texts:
            chunk = {"text": t, "source": source, "start": 0}
            self.chunks.append(chunk)
            self.tokenized.append(tokenize(t))
        self._fitted = False

    def fit(self) -> None:
        """在所有 chunk 上训练向量器和 BM25。新增文档后必须调用。"""
        if not self.tokenized:
            return
        self.vectorizer.fit(self.tokenized)
        self._vecs = [self.vectorizer.transform(tok) for tok in self.tokenized]
        self.bm25.fit(self.tokenized)
        self._fitted = True

    # ------------------------------------------------------------------
    def query(self, question: str, k: int = 3, rerank: bool = True, recall_n: int = 10) -> list[dict[str, Any]]:
        """检索最相关的 k 个 chunk。

        两阶段：
          1. 向量余弦召回 top `recall_n`（默认 10）候选；
          2. 若 rerank=True，用 BM25 对候选重排，取最终 top k。
        """
        if not self._fitted:
            self.fit()
        if not self.chunks:
            return []

        q_tokens = tokenize(question)
        q_vec = self.vectorizer.transform(q_tokens)

        # 阶段 1：向量召回
        scored = [(cosine(q_vec, v), i) for i, v in enumerate(self._vecs)]
        scored.sort(key=lambda x: -x[0])
        candidates = [i for _, i in scored[: max(k, recall_n)]]

        if not rerank:
            return [self._to_result(i, float(s)) for s, i in scored[:k]]

        # 阶段 2：BM25 重排候选
        reranked = [(self.bm25.score(q_tokens, i), i) for i in candidates]
        reranked.sort(key=lambda x: -x[0])
        return [self._to_result(i, s) for s, i in reranked[:k]]

    def _to_result(self, idx: int, score: float) -> dict[str, Any]:
        return {
            "score": round(score, 4),
            "chunk": self.chunks[idx],
        }

    def __len__(self) -> int:
        return len(self.chunks)
