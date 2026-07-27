"""RAG 评测指标 — Recall@k、MRR@k、nDCG@k。

修复 audit #12：
  - 基于 qrels（相关性标注）计算指标，不依赖子串匹配
  - 支持多级相关性（0/1/2）
  - 指标脚本可重复执行
"""

from __future__ import annotations

import math
from typing import Any


def recall_at_k(retrieved_doc_ids: list[str], relevant_doc_ids: set[str], k: int) -> float:
    """Recall@k：前 k 个结果中命中相关文档的比例。"""
    if not relevant_doc_ids:
        return 0.0
    top_k = set(retrieved_doc_ids[:k])
    hits = len(top_k & relevant_doc_ids)
    return hits / len(relevant_doc_ids)


def precision_at_k(retrieved_doc_ids: list[str], relevant_doc_ids: set[str], k: int) -> float:
    """Precision@k：前 k 个结果中相关文档的比例。"""
    if k == 0:
        return 0.0
    top_k = set(retrieved_doc_ids[:k])
    hits = len(top_k & relevant_doc_ids)
    return hits / k


def mrr_at_k(retrieved_doc_ids: list[str], relevant_doc_ids: set[str], k: int) -> float:
    """MRR@k（Mean Reciprocal Rank）：第一个相关文档的倒数排名。"""
    for i, doc_id in enumerate(retrieved_doc_ids[:k]):
        if doc_id in relevant_doc_ids:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(retrieved_doc_ids: list[str], qrels: dict[str, int], k: int) -> float:
    """nDCG@k（Normalized Discounted Cumulative Gain）：考虑排序位置和相关性等级。

    qrels: {doc_id: relevance_score}，relevance_score 越大越相关。
    """
    if not qrels:
        return 0.0

    # DCG@k
    dcg = 0.0
    for i, doc_id in enumerate(retrieved_doc_ids[:k]):
        rel = qrels.get(doc_id, 0)
        if rel > 0:
            dcg += (2 ** rel - 1) / math.log2(i + 2)  # i+2 因为 log2(1)=0 但 i 从 0 开始

    # IDCG@k（理想排序）
    ideal_rels = sorted(qrels.values(), reverse=True)[:k]
    idcg = 0.0
    for i, rel in enumerate(ideal_rels):
        if rel > 0:
            idcg += (2 ** rel - 1) / math.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


def evaluate_retrieval(
    results: list[list[str]],
    qrels: dict[str, dict[str, int]],
    k_values: list[int] | None = None,
) -> dict[str, Any]:
    """对多组检索结果计算指标。

    Args:
        results: 每组检索结果的 doc_id 列表，顺序与 query_ids 对应
        qrels: {query_id: {doc_id: relevance_score}}
        k_values: 评估的 k 值列表

    Returns:
        指标字典，包含 Recall@k、MRR@k、nDCG@k
    """
    if k_values is None:
        k_values = [1, 3, 5]

    query_ids = list(qrels.keys())
    metrics = {k: {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0} for k in k_values}
    details: list[dict[str, Any]] = []

    for i, qid in enumerate(query_ids):
        if i >= len(results):
            break
        retrieved = results[i]
        qrel = qrels[qid]
        relevant = {doc_id for doc_id, rel in qrel.items() if rel > 0}

        per_k = {}
        for k in k_values:
            r = recall_at_k(retrieved, relevant, k)
            m = mrr_at_k(retrieved, relevant, k)
            n = ndcg_at_k(retrieved, qrel, k)
            per_k[k] = {"recall": round(r, 4), "mrr": round(m, 4), "ndcg": round(n, 4)}
            metrics[k]["recall"] += r
            metrics[k]["mrr"] += m
            metrics[k]["ndcg"] += n

        details.append({"query_id": qid, "per_k": per_k})

    n = len(query_ids) or 1
    for k in k_values:
        metrics[k]["recall"] = round(metrics[k]["recall"] / n, 4)
        metrics[k]["mrr"] = round(metrics[k]["mrr"] / n, 4)
        metrics[k]["ndcg"] = round(metrics[k]["ndcg"] / n, 4)

    return {
        "total_queries": len(query_ids),
        "k_values": k_values,
        "metrics": {f"@{k}": metrics[k] for k in k_values},
        "details": details,
    }
