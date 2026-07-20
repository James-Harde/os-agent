"""简单 BM25 重排。

对向量检索的候选 top-N 用 BM25 重新打分排序，弥补 TF-IDF 余弦对词频饱和
与文档长度敏感的不足。这是经典「检索 + 重排」两阶段模式的最小实现。
"""

from __future__ import annotations

import math
from typing import List


class BM25:
    """BM25Okapi 简化版（b=0.75, k1=1.5）。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.idf: dict[str, float] = []
        self._avgdl: float = 1.0
        self._docs: list[list[str]] = []

    def fit(self, docs: list[list[str]]) -> "BM25":
        self._docs = docs
        n = max(len(docs), 1)
        df: dict[str, int] = {}
        dl_sum = 0
        for tokens in docs:
            dl_sum += len(tokens)
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1
        self._avgdl = dl_sum / n or 1.0
        self.idf = {
            t: math.log((n - freq + 0.5) / (freq + 0.5) + 1.0)
            for t, freq in df.items()
        }
        return self

    def score(self, query_tokens: list[str], index: int) -> float:
        tokens = self._docs[index]
        dl = len(tokens) or 1
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t in query_tokens:
            if t not in self.idf:
                continue
            f = tf.get(t, 0)
            score += self.idf[t] * (f * (self.k1 + 1)) / (
                f + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
            )
        return score
