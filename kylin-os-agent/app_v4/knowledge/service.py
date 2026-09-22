"""知识库导入服务 — 上传 → 解析 → 切分 → 向量化 → Milvus 入库 的编排。

职责：
  - 校验并落盘上传文件（受控目录，UUID 存储名）。
  - 创建 document / job 清单记录（queued）。
  - 在后台任务中（线程池执行阻塞的解析 / Embedding / Milvus 写入）驱动状态机：
        queued → parsing → embedding → indexed
                               ↘ failed（任何阶段失败必须可见）
  - 提供 list / get / delete / search / job 查询。
  - 删除按 document_id 精确过滤 Milvus，不清空集合；同步删除清单与文件。

注入：
  - store  : DocumentMetadataStore（SQLite 清单）
  - rag    : MilvusRAGStore（生产）或兼容 double（测试）
  - upload_dir : 受控上传目录
  - single_max_bytes / total_max_bytes : 大小限制
  - executor    : 线程池（阻塞 IO 不阻塞事件循环）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from app_v4.knowledge import models
from app_v4.knowledge.parsing import parse_document
from app_v4.knowledge.security import (
    AcceptedUpload,
    UploadRejectedError,
    save_to_upload_dir,
    validate_and_accept,
)
from app_v4.knowledge.store import DocumentMetadataStore

logger = logging.getLogger("app_v4.knowledge.service")


class ImportFailedError(RuntimeError):
    """导入失败（已记录到 job/document 状态）。"""


class KnowledgeService:
    def __init__(
        self,
        *,
        store: DocumentMetadataStore,
        rag: Any,  # MilvusRAGStore or compatible double
        upload_dir: Path | str,
        single_max_bytes: int = 10 * 1024 * 1024,      # 10 MB / 文件
        total_max_bytes: int = 100 * 1024 * 1024,        # 100 MB 总量
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.store = store
        self.rag = rag
        self.upload_dir = Path(upload_dir)
        self.single_max_bytes = single_max_bytes
        self.total_max_bytes = total_max_bytes
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="knowledge-import")
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # 上传入口
    # ------------------------------------------------------------------
    async def upload(
        self,
        *,
        filename: str,
        declared_content_type: str | None,
        data: bytes,
    ) -> dict[str, Any]:
        """校验、落盘、建清单，启动后台导入任务，返回 document_id/job_id。

        注意：返回时仅表示"已接收"；入库完成需轮询 job 状态至 indexed/failed。
        """
        # 1) 安全校验（同步、快速；数据已在内存）
        try:
            accepted = validate_and_accept(
                filename=filename,
                declared_content_type=declared_content_type,
                data=data,
                current_total_bytes=self.store.total_bytes(),
                single_max_bytes=self.single_max_bytes,
                total_max_bytes=self.total_max_bytes,
            )
        except UploadRejectedError as exc:
            # 安全/校验拒绝：不建清单，直接抛（由 router 转 400）。
            raise

        # 2) 落盘（受控目录，UUID 存储名）
        stored_path = save_to_upload_dir(self.upload_dir, accepted.safe_filename, data)

        # 3) 建清单（document + job，状态 queued）
        job_id = str(uuid.uuid4())
        self.store.insert_document(
            document_id=accepted.document_id,
            original_filename=accepted.original_filename,
            stored_filename=accepted.safe_filename,
            content_type=accepted.ext,
            size=accepted.size,
            sha256=accepted.sha256,
            status=models.ImportStatus.queued.value,
        )
        self.store.insert_job(job_id=job_id, document_id=accepted.document_id,
                              status=models.ImportStatus.queued.value)

        # 4) 启动后台任务（不阻塞事件循环）
        self._schedule_import(job_id, accepted.document_id, stored_path, accepted)

        return {
            "document_id": accepted.document_id,
            "job_id": job_id,
            "filename": accepted.original_filename,
            "size": accepted.size,
        }

    def _schedule_import(
        self, job_id: str, document_id: str, stored_path: Path, accepted: AcceptedUpload,
    ) -> None:
        """把导入任务提交到 asyncio 事件循环。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无事件循环（如纯同步测试）：同步执行。
            self._run_import_sync(job_id, document_id, stored_path, accepted.ext, accepted.original_filename)
            return
        loop.create_task(
            self._run_import(job_id, document_id, stored_path, accepted.ext, accepted.original_filename)
        )

    # ------------------------------------------------------------------
    # 后台导入（状态机）
    # ------------------------------------------------------------------
    async def _run_import(
        self, job_id: str, document_id: str, stored_path: Path, ext: str, original_filename: str,
    ) -> None:
        """异步编排：parsing → embedding，阻塞部分放到线程池。"""
        try:
            # parsing
            self.store.update_job(job_id, status=models.ImportStatus.parsing.value,
                                  progress={"stage": "parsing"})
            self.store.update_document_status(document_id, status=models.ImportStatus.parsing.value)
            docs = await self._loop.run_in_executor(
                self._executor,
                lambda: parse_document(
                    path=stored_path, ext=ext,
                    document_id=document_id, original_filename=original_filename,
                ),
            )
            if not docs:
                raise ImportFailedError("文档解析未产出可索引内容")

            # embedding + Milvus 写入
            self.store.update_job(job_id, status=models.ImportStatus.embedding.value,
                                  progress={"stage": "embedding", "chunks": len(docs)})
            self.store.update_document_status(document_id, status=models.ImportStatus.embedding.value)

            def _ingest() -> int:
                return self.rag.ingest(docs)

            added = await self._loop.run_in_executor(self._executor, _ingest)

            # 成功：只有这里才标记 indexed（已可检索）
            self.store.update_document_status(
                document_id, status=models.ImportStatus.indexed.value, chunk_count=added,
            )
            self.store.update_job(
                job_id, status=models.ImportStatus.indexed.value,
                progress={"stage": "indexed", "chunks": added},
            )
            logger.info("导入入库完成：%s（%s，新增 %d chunk）", original_filename, document_id, added)
        except Exception as exc:
            logger.exception("导入失败：%s（%s）", original_filename, document_id)
            msg = f"{type(exc).__name__}: {exc}"
            self.store.update_document_status(
                document_id, status=models.ImportStatus.failed.value, error=msg,
            )
            self.store.update_job(
                job_id, status=models.ImportStatus.failed.value,
                progress={"stage": "failed"}, error=msg,
            )

    def _run_import_sync(
        self, job_id: str, document_id: str, stored_path: Path, ext: str, original_filename: str,
    ) -> None:
        """同步回退（无事件循环时）。"""
        try:
            self.store.update_job(job_id, status=models.ImportStatus.parsing.value)
            self.store.update_document_status(document_id, status=models.ImportStatus.parsing.value)
            docs = parse_document(path=stored_path, ext=ext,
                                  document_id=document_id, original_filename=original_filename)
            self.store.update_job(job_id, status=models.ImportStatus.embedding.value)
            self.store.update_document_status(document_id, status=models.ImportStatus.embedding.value)
            added = self.rag.ingest(docs)
            self.store.update_document_status(
                document_id, status=models.ImportStatus.indexed.value, chunk_count=added,
            )
            self.store.update_job(job_id, status=models.ImportStatus.indexed.value,
                                  progress={"stage": "indexed", "chunks": added})
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.store.update_document_status(
                document_id, status=models.ImportStatus.failed.value, error=msg,
            )
            self.store.update_job(job_id, status=models.ImportStatus.failed.value, error=msg)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_documents(self) -> list[dict[str, Any]]:
        return self.store.list_documents()

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        return self.store.get_document(document_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.store.get_job(job_id)

    # ------------------------------------------------------------------
    # 删除（精确、不清集合）
    # ------------------------------------------------------------------
    def delete_document(self, document_id: str) -> dict[str, Any]:
        """删除文档及其 Milvus chunks。

        精确过滤 document_id，不清空集合。返回删除的 chunk 数。
        即使 Milvus 删除返回 0（如已为空），也清理清单与文件。
        """
        doc = self.store.get_document(document_id)
        stored_filename = doc["stored_filename"] if doc else None

        deleted = 0
        try:
            deleted = self.rag.delete_by_document_id(document_id)
        except Exception as exc:
            # Milvus 不可达：仍尝试清理清单/文件，但把错误抛给上层。
            logger.warning("Milvus 删除失败（%s）：%s", document_id, exc)
            self.store.delete_document(document_id)
            self._remove_file(stored_filename)
            raise

        self.store.delete_document(document_id)
        self._remove_file(stored_filename)
        return {"document_id": document_id, "deleted_chunks": deleted}

    def _remove_file(self, stored_filename: str | None) -> None:
        if not stored_filename:
            return
        try:
            target = (self.upload_dir / stored_filename).resolve()
            if target.exists():
                target.unlink()
        except Exception as exc:
            logger.warning("删除上传文件失败：%s (%s)", stored_filename, exc)

    # ------------------------------------------------------------------
    # 检索（复用 Milvus 混合检索，返回带文件名的引用）
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        results = self.rag.search(query, top_k=top_k)
        return results
