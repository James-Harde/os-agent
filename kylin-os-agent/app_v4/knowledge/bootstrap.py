"""构建 KnowledgeService 的依赖（懒建 rag_store，fail-fast 外抛）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app_v4.container import Dependencies
from app_v4.knowledge.service import KnowledgeService
from app_v4.knowledge.store import DocumentMetadataStore

logger = logging.getLogger("app_v4.knowledge.bootstrap")


def build_knowledge_service(
    deps: Dependencies,
    *,
    upload_dir: Path | str | None = None,
    knowledge_db_path: Path | str | None = None,
) -> KnowledgeService:
    """构建 KnowledgeService。

    rag_store 通过容器懒建：Milvus 不可达 / Embedding 未配置时，此处会抛出
    （store_factory 的 fail-fast 异常），由调用方决定处理（启动时或请求时）。
    """
    settings = deps.settings
    store = DocumentMetadataStore(
        knowledge_db_path or settings.resolved_knowledge_db_path()
    )
    rag = deps.rag_store  # 懒建；失败则抛出
    return KnowledgeService(
        store=store,
        rag=rag,
        upload_dir=upload_dir or settings.resolved_knowledge_upload_dir(),
        single_max_bytes=settings.knowledge_single_max_bytes,
        total_max_bytes=settings.knowledge_total_max_bytes,
    )
