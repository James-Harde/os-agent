"""文档切片 — 按字符滑动窗口切分，带重叠区。

策略：
  - 简单按字符切分（chunk_size 字符 + overlap 字符重叠）。
  - 不依赖句子边界：对结构化知识库（FAQ、文档段落）字符切分足够，
    且避免引入分句依赖。
  - 保留 source 字段用于评测时定位 ground-truth chunk。
"""

from __future__ import annotations

from typing import Any


def chunk_text(
    text: str,
    chunk_size: int = 300,
    overlap: int = 40,
    source: str = "",
) -> list[dict[str, Any]]:
    """把长文本切成带重叠的 chunk 列表。

    每个 chunk: {"text": ..., "source": ..., "start": int}
    """
    text = text.strip()
    if not text:
        return []

    size = max(50, chunk_size)
    step = max(1, size - overlap)
    chunks: list[dict[str, Any]] = []
    i = 0
    while i < len(text):
        seg = text[i:i + size]
        if seg.strip():
            chunks.append({"text": seg, "source": source, "start": i})
        i += step
        if i >= len(text) and (i - step) < len(text):
            # 末尾不足一步：确保最后一段被覆盖（while 条件已处理）
            break
    return chunks
