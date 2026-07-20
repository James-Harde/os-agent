"""RAG 评测 — Recall@k。

输入：一组样本 [{"question":..., "relevant_text":.../relevant_ids:[...]}]
逻辑：对每个问题检索 top-k，判断 ground-trunk chunk 是否在结果中。
输出：Recall@1, Recall@3, Recall@5。
"""

from __future__ import annotations

from typing import Any

from app_v4.rag.pipeline import RAGIndex


def recall_at_k(retrieved_texts: list[str], relevant_texts: list[str], k: int) -> bool:
    """ground-truth 文本集合是否出现在前 k 个结果中（子串匹配，容错边界）。"""
    top = retrieved_texts[:k]
    for rel in relevant_texts:
        rel_norm = rel.strip()[:60]  # 取前 60 字符做子串匹配
        for t in top:
            if rel_norm and rel_norm in t:
                return True
    return False


def evaluate(samples: list[dict[str, Any]], k_values: list[int] | None = None) -> dict[str, Any]:
    """在已 fit 的样本集上计算 Recall@k。

    期望 samples 格式：
      [{"question": "...", "relevant": ["包含答案的 chunk 文本", ...]}, ...]
    """
    if k_values is None:
        k_values = [1, 3, 5]
    hits = {k: 0 for k in k_values}
    total = len(samples)
    details: list[dict[str, Any]] = []

    for s in samples:
        q = s["question"]
        rel = s["relevant"] if isinstance(s["relevant"], list) else [s["relevant"]]
        # 这里 index 由调用者注入：把索引好的 RAGIndex 存入 s["_index"]
        index: RAGIndex = s["_index"]
        results = index.query(q, k=max(k_values))
        texts = [r["chunk"]["text"] for r in results]
        per_k = {}
        for k in k_values:
            hit = recall_at_k(texts, rel, k)
            per_k[k] = hit
            if hit:
                hits[k] += 1
        details.append({"question": q, "hits": per_k, "top1": texts[:1]})

    return {
        "total": total,
        "recall": {f"Recall@{k}": (hits[k] / total if total else 0.0) for k in k_values},
        "details": details,
    }


# ---------------------------------------------------------------------------
# 内置示例数据集（8 条样本，覆盖运维常见问题）
# ---------------------------------------------------------------------------
SAMPLE_DATASET: list[dict[str, Any]] = [
    {
        "question": "如何查看磁盘使用率",
        "relevant": "磁盘使用率可以通过 df -h 命令查看，显示每个分区的已用和可用空间百分比。",
    },
    {
        "question": "哪个进程最占 CPU",
        "relevant": "查看 CPU 占用最高的进程推荐使用 top 命令，按 P 键按 CPU 排序，或使用 ps aux --sort=-%cpu。",
    },
    {
        "question": "端口 8080 被谁占用了",
        "relevant": "查询端口占用可用 ss -ltnp | grep 8080 或 netstat -ano | grep 8080 查看对应进程。",
    },
    {
        "question": "怎么查看系统错误日志",
        "relevant": "系统错误日志可用 journalctl -p warning..alert 查看 warning 及以上级别，也可查看 /var/log/syslog。",
    },
    {
        "question": "服务重启需要什么权限",
        "relevant": "systemctl restart 等写操作需要 root 权限，普通用户需通过 sudo 提权或加入 systemctl 组。",
    },
    {
        "question": "如何排查网络延迟高",
        "relevant": "排查网络延迟先用 ping 测 RTT，再用 traceroute 定位跳点，最后用 mtr 持续观测丢包。",
    },
    {
        "question": "什么是提示词注入风险",
        "relevant": "提示词注入是指攻击者在工具输出或文档中植入伪装指令，诱导 LLM 忽略原有规则执行恶意命令。",
    },
    {
        "question": "哪些工具是只读的不会修改系统",
        "relevant": "disk_usage、process_list、port_lookup、system_logs、service_status 等只读工具会自动执行，不会修改系统状态。",
    },
]


def build_sample_index() -> RAGIndex:
    """用内置语料构建一个示例索引（每条样本的 relevant 视为一份文档）。"""
    idx = RAGIndex(chunk_size=200, overlap=30)
    for s in SAMPLE_DATASET:
        idx.add_document(s["relevant"], source=s["question"])
    idx.fit()
    return idx


def run_evaluation() -> dict[str, Any]:
    """端到端评测便捷入口：建索引 + 评测。返回结果字典。"""
    idx = build_sample_index()
    samples = [dict(s) for s in SAMPLE_DATASET]
    for s in samples:
        s["_index"] = idx
    return evaluate(samples, k_values=[1, 3])
