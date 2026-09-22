"""知识库 FastAPI 路由 — /api/knowledge/...。

端点：
  POST   /api/knowledge/documents                上传并创建导入任务
  GET    /api/knowledge/documents                列出文件 / 状态 / chunk_count
  GET    /api/knowledge/documents/{document_id}  单文档详情
  DELETE /api/knowledge/documents/{document_id}  删除文档及其 Milvus chunks
  GET    /api/knowledge/jobs/{job_id}            任务状态
  POST   /api/knowledge/search                   混合检索（带 source/document_id/page/chunk_id 引用）

服务按请求构建：从当前请求的 app.state.deps 装配 rag_store（容器内缓存）。
rag_store 懒建；Milvus / Embedding 未配置时返回 503（知识库暂不可用），
不影响应用启动与其它端点。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from app_v4.knowledge import models
from app_v4.knowledge.bootstrap import build_knowledge_service
from app_v4.knowledge.security import ALLOWED_EXTENSIONS, UploadRejectedError
from app_v4.knowledge.service import KnowledgeService

logger = logging.getLogger("app_v4.knowledge.router")


class _Unavailable(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=503, detail=f"知识库暂不可用：{detail}")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


def _build_service(request: Request) -> KnowledgeService:
    """按当前请求的 deps 构建服务；rag_store 构建失败返回 503。"""
    deps = request.app.state.deps
    try:
        return build_knowledge_service(deps)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("KnowledgeService 构建失败（RAG 不可用）")
        raise _Unavailable(f"{type(exc).__name__}: {exc}") from exc


def build_router(get_deps) -> APIRouter:
    """构建路由（保持接口一致；实际按请求从 request.app.state.deps 取容器）。"""

    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

    @router.post("/documents", response_model=models.KnowledgeUploadResponse)
    async def upload_document(request: Request, file: UploadFile = File(...)) -> dict:
        """上传一个文档（.pdf / .docx / .txt / .md），创建导入任务。

        返回 document_id / job_id；入库完成需轮询 GET jobs/{job_id} 至 indexed/failed。
        """
        service = _build_service(request)
        if not file.filename:
            raise HTTPException(status_code=400, detail="缺少文件名")

        data = await file.read()
        try:
            result = await service.upload(
                filename=file.filename,
                declared_content_type=file.content_type,
                data=data,
            )
        except UploadRejectedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @router.get("/documents", response_model=models.DocumentList)
    def list_documents(request: Request) -> dict:
        items = _build_service(request).list_documents()
        return {"total": len(items), "items": [models.DocumentItem(**_doc_to_api(it)) for it in items]}

    @router.get("/documents/{document_id}", response_model=models.DocumentItem)
    def get_document(request: Request, document_id: str) -> dict:
        doc = _build_service(request).get_document(document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"document not found: {document_id}")
        return models.DocumentItem(**_doc_to_api(doc))

    @router.delete("/documents/{document_id}")
    def delete_document(request: Request, document_id: str) -> dict:
        """删除文档及其 Milvus chunks（按 document_id 精确过滤，不清空集合）。"""
        service = _build_service(request)
        doc = service.get_document(document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"document not found: {document_id}")
        try:
            result = service.delete_document(document_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"删除失败（Milvus 错误）：{type(exc).__name__}",
            ) from exc
        return {"status": "deleted", **result}

    @router.get("/jobs/{job_id}", response_model=models.JobItem)
    def get_job(request: Request, job_id: str) -> dict:
        job = _build_service(request).get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return models.JobItem(**_job_to_api(job))

    @router.post("/search", response_model=models.KnowledgeSearchResponse)
    def search(request: Request, req: SearchRequest) -> dict:
        """混合检索（复用 Milvus dense + BM25 + RRF），返回带文件名的引用。"""
        try:
            results = _build_service(request).search(req.query, top_k=req.top_k)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"检索暂不可用：{type(exc).__name__}",
            ) from exc
        return {
            "query": req.query,
            "results": [models.ChunkResult(**r) for r in results],
        }

    return router


def _doc_to_api(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": d["document_id"],
        "original_filename": d["original_filename"],
        "content_type": d["content_type"],
        "size": d["size"],
        "sha256": d["sha256"],
        "status": d["status"],
        "chunk_count": d.get("chunk_count", 0),
        "error": d.get("error"),
        "created_at": d.get("created_at", ""),
        "updated_at": d.get("updated_at", ""),
    }


def _job_to_api(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": d["job_id"],
        "document_id": d["document_id"],
        "status": d["status"],
        "progress": d.get("progress", {}),
        "error": d.get("error"),
        "created_at": d.get("created_at", ""),
        "updated_at": d.get("updated_at", ""),
    }
