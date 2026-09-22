"""文档解析 — 使用官方 LangChain Loader，不手写 PDF/DOCX 解析。

选择：
  - .pdf   → PyPDFLoader（pypdf，按页切分，page 元数据自动注入）
  - .docx  → Docx2txtLoader（docx2txt）
  - .txt/.md → TextLoader（UTF-8）

每个解析得到的 Document 增强 metadata：
  document_id / source / original_filename / page（若有）/ content_hash。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document

logger = logging.getLogger("app_v4.knowledge.parsing")


class ParseError(RuntimeError):
    """文档解析失败（Loader 抛异常或未产出任何片段）。"""


def _load_with(path: Path, ext: str) -> list[Document]:
    """按扩展名选择官方 Loader 并加载。"""
    if ext == ".pdf":
        from langchain_community.document_loaders import PyPDFLoader
        return PyPDFLoader(str(path)).load()
    if ext == ".docx":
        from langchain_community.document_loaders import Docx2txtLoader
        return Docx2txtLoader(str(path)).load()
    if ext in (".txt", ".md"):
        from langchain_community.document_loaders import TextLoader
        return TextLoader(str(path), encoding="utf-8").load()
    raise ParseError(f"无可用 Loader：扩展名 {ext}")


def parse_document(
    *,
    path: Path,
    ext: str,
    document_id: str,
    original_filename: str,
) -> list[Document]:
    """解析文件为 LangChain Document 列表，并增强元数据。

    增强字段：
      - document_id    : 本次导入的 UUID 主键
      - source         : 来源标识（original_filename）
      - original_filename : 用户原始文件名（展示用）
      - page           : 来源页码（PDF/Word 有；纯文本留空字符串）
      - content_hash   : 文本内容哈希（便于验证）

    抛出 ParseError：Loader 失败或未产出任何非空片段。
    """
    try:
        docs = _load_with(path, ext)
    except Exception as exc:
        raise ParseError(f"文档解析失败（{ext}）：{exc}") from exc

    if not docs:
        raise ParseError("文档解析未产出任何内容")

    enriched: list[Document] = []
    for idx, doc in enumerate(docs):
        text = (doc.page_content or "").strip()
        if not text:
            continue
        meta = dict(doc.metadata or {})
        # page：PDF loader 注入 'page'（0-based）；统一为 1-based 字符串供展示。
        page_raw = meta.get("page")
        page_str = ""
        if page_raw is not None:
            try:
                page_str = str(int(page_raw) + 1)
            except (TypeError, ValueError):
                page_str = str(page_raw)
        enriched.append(Document(
            page_content=text,
            metadata={
                "document_id": document_id,
                "source": original_filename,
                "original_filename": original_filename,
                "page": page_str,
                "content_hash": _text_hash(text),
                # 保留 loader 原始字段（如 PDF 的 file_path / page）。
                **{k: v for k, v in meta.items() if k not in ("page",)},
            },
        ))

    if not enriched:
        raise ParseError("文档无可索引文本内容（解析结果为空）")

    logger.info("文档解析完成：%s（%s，%d 个片段）", original_filename, ext, len(enriched))
    return enriched


def _text_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def iter_supported_extensions() -> Iterable[str]:
    return [".pdf", ".docx", ".txt", ".md"]
