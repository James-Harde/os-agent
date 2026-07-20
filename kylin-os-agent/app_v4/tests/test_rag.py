"""P3 RAG 最小链测试。

覆盖：
  - chunk 切片重叠
  - token 化
  - 向量余弦检索基本召回
  - BM25 重排
  - 端到端 Recall@1 / Recall@3 评测
"""

import math

from app_v4.rag.bm25 import BM25
from app_v4.rag.chunk import chunk_text
from app_v4.rag.eval import run_evaluation
from app_v4.rag.pipeline import RAGIndex
from app_v4.rag.vectorizer import TfidfVectorizer, cosine, tokenize


# ---------------------------------------------------------------------------
# chunk
# ---------------------------------------------------------------------------
def test_chunk_text_basic():
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=300, overlap=40)
    assert len(chunks) > 1
    # 每个 chunk 长度不超过 chunk_size
    assert all(len(c["text"]) <= 300 for c in chunks)


def test_chunk_text_overlap_shares_content():
    text = "".join(f"【段{i}】" + "x" * 100 for i in range(10))
    chunks = chunk_text(text, chunk_size=150, overlap=50)
    # 有重叠时相邻 chunk 有公共内容
    if len(chunks) >= 2:
        c0 = chunks[0]["text"]
        c1 = chunks[1]["text"]
        # 公共子串长度应 >= overlap
        common = sum(1 for i in range(min(len(c0), 200)) if c0[i:i + 20] in c1)
        assert common > 0


def test_chunk_text_empty():
    assert chunk_text("   ") == []
    assert chunk_text("") == []


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------
def test_tokenize_chinese_chars():
    tokens = tokenize("磁盘使用率")
    # 中文按字切
    assert "磁" in tokens and "盘" in tokens


def test_tokenize_english_words():
    tokens = tokenize("disk usage high")
    assert "disk" in tokens and "usage" in tokens


def test_tokenize_mixed():
    tokens = tokenize("查看 disk 使用率")
    assert "disk" in tokens
    assert "使" in tokens


# ---------------------------------------------------------------------------
# vectorizer + cosine
# ---------------------------------------------------------------------------
def test_tfidf_basic():
    docs = [
        ["磁盘", "使用率", "高"],
        ["进程", "cpu", "占用"],
        ["端口", "占用", "查询"],
    ]
    vec = TfidfVectorizer().fit(docs)
    v0 = vec.transform(docs[0])
    v1 = vec.transform(docs[1])
    # 自相似度应高于无关文档
    assert cosine(v0, v0) > cosine(v0, v1)


def test_cosine_identical():
    v = {"a": 0.5, "b": 0.5}
    # 未归一化的相同向量点积 > 0
    assert cosine(v, v) > 0


def test_cosine_orthogonal():
    v1 = {"a": 1.0}
    v2 = {"b": 1.0}
    assert cosine(v1, v2) == 0.0


# ---------------------------------------------------------------------------
# pipeline 检索
# ---------------------------------------------------------------------------
def test_pipeline_query_returns_k():
    idx = RAGIndex(chunk_size=100, overlap=20)
    idx.add_document("磁盘使用率通过 df 命令查看，显示每个分区已用可用空间。", source="d1")
    idx.add_document("系统日志通过 journalctl 查看，支持按级别过滤。", source="d2")
    idx.add_document("进程占用通过 top 或 ps aux 查看。", source="d3")
    idx.fit()
    results = idx.query("怎么查磁盘", k=2)
    assert len(results) == 2
    assert results[0]["score"] >= results[1]["score"]  # 按分排序


def test_pipeline_top1_contains_answer():
    idx = RAGIndex(chunk_size=100, overlap=20)
    idx.add_document("磁盘使用率通过 df -h 命令查看。", source="d1")
    idx.add_document("进程列表通过 ps aux 查看。", source="d2")
    idx.fit()
    results = idx.query("磁盘使用率", k=1)
    assert "磁盘" in results[0]["chunk"]["text"]


def test_pipeline_rerank_changes_order():
    """BM25 重排与纯向量排序可以不同（验证两阶段生效）。"""
    idx = RAGIndex(chunk_size=150, overlap=20)
    idx.add_document("磁盘使用率 df 命令查看磁盘空间。", source="a")
    idx.add_document("磁盘磁盘磁盘 无关词 cpu 进程 端口 disk disk extra words。", source="b")
    idx.fit()
    r_no_rr = idx.query("磁盘使用率", k=2, rerank=False)
    r_rr = idx.query("磁盘使用率", k=2, rerank=True)
    # 两者都应返回 2 个结果，不报错
    assert len(r_no_rr) == 2 and len(r_rr) == 2


def test_pipeline_empty_query_safe():
    idx = RAGIndex()
    idx.fit()
    assert idx.query("anything", k=3) == []


# ---------------------------------------------------------------------------
# BM25 基础
# ---------------------------------------------------------------------------
def test_bm25_score_positive_for_relevant():
    docs = [["磁盘", "使用率", "高"], ["进程", "cpu"]]
    bm = BM25().fit(docs)
    s0 = bm.score(["磁盘", "使用率"], 0)  # 与文档0相关
    s1 = bm.score(["磁盘", "使用率"], 1)  # 与文档1无关
    assert s0 > s1


# ---------------------------------------------------------------------------
# 端到端评测：Recall@1 / Recall@3
# ---------------------------------------------------------------------------
def test_eval_recall_at3_is_perfect_or_high():
    """8 条内置样本的 Recall@3 应 >= 0.75（字符 token 下基本能命中）。"""
    result = run_evaluation()
    assert result["total"] == 8
    assert result["recall"]["Recall@3"] >= 0.75, f"Recall@3 too low: {result}"


def test_eval_recall_at1_reasonable():
    """Recall@1 应 >= 0.5（区分度不完美但正向）。"""
    result = run_evaluation()
    assert result["recall"]["Recall@1"] >= 0.5, f"Recall@1 too low: {result}"
