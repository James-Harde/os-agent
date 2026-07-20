"""RAG 最小链 — 零 heavy 依赖。

模块：
  - chunk       字符滑动窗口切片（带重叠）
  - vector      纯 Python TF-IDF + 余弦相似度
  - bm25        简单 BM25 重排
  - store       SQLite 文档/切片持久化（可选）
  - pipeline    索引 + 检索编排
  - eval        Recall@k 评测

设计取舍：
  - 不引入 faiss/chroma/sentence-transformers，整个包仅用 Python 标准库。
  - 中文采用「单字 + 二元组」混合 token，无需 jieba 也能获得不错召回。
  - 向量用稀疏 dict 表示，余弦相似度 O(|query_terms|)，小规模完全够用。
"""

from app_v4.rag.pipeline import RAGIndex

__all__ = ["RAGIndex"]
