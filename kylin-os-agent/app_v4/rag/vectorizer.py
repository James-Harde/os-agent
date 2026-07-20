"""纯 Python 向量化 — TF-IDF + 余弦相似度（稀疏 dict 表示）。

不依赖 numpy/scipy：向量用 {term: weight} dict，余弦相似度只需遍历
query 的非零项，复杂度 O(|query 词项数|)，小规模知识库完全够用。
"""

from __future__ import annotations

import math
import re
from typing import Iterable


_TOKEN_RE = re.compile(r"[一-鿿]|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """中英混合 token化。

    - 中文：拆成单字（保留信息密度低但稳定）
    - 英文/数字：按正则切词并 lower
    生产环境可替换为 jieba / BPE，接口不变。
    """
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        tokens.append(m.group(0))
    return tokens


class TfidfVectorizer:
    """稀疏 TF-IDF 向量器。"""

    def __init__(self) -> None:
        self.idf: dict[str, float] = {}
        self._n_docs: int = 0

    def fit(self, docs: Iterable[list[str]]) -> "TfidfVectorizer":
        """从已 token 化的文档集合学习 IDF。"""
        df: dict[str, int] = {}
        n = 0
        for tokens in docs:
            n += 1
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1
        self._n_docs = max(n, 1)
        # 平滑 IDF
        self.idf = {
            term: math.log((1 + self._n_docs) / (1 + freq)) + 1.0
            for term, freq in df.items()
        }
        return self

    def transform(self, tokens: list[str]) -> dict[str, float]:
        """把 token 列表转成稀疏 TF-IDF 向量（未归一化）。"""
        tf: dict[str, float] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0.0) + 1.0
        vec = {t: w * self.idf.get(t, 1.0) for t, w in tf.items()}
        return self._normalize(vec)

    def _normalize(self, vec: dict[str, float]) -> dict[str, float]:
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}


def cosine(query_vec: dict[str, float], doc_vec: dict[str, float]) -> float:
    """两个稀疏向量的余弦相似度（输入应已归一化，此时等价于点积）。"""
    # 遍历较短向量
    if len(query_vec) > len(doc_vec):
        query_vec, doc_vec = doc_vec, query_vec
    return sum(v * doc_vec.get(t, 0.0) for t, v in query_vec.items())
