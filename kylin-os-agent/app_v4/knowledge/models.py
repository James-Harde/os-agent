"""知识库请求/响应模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ImportStatus(str, Enum):
    """导入任务状态。

    诚实区分：
      - queued    : 已接收上传，等待解析
      - parsing   : 正在解析文档（Loader）
      - embedding : 正在切分 + 向量化 + Milvus 写入
      - indexed   : Milvus 写入成功，可检索（唯一表示"已入库"的状态）
      - failed    : 失败（message 含原因）
    """
    queued = "queued"
    parsing = "parsing"
    embedding = "embedding"
    indexed = "indexed"
    failed = "failed"


class KnowledgeUploadResponse(BaseModel):
    """上传响应：返回 document_id 与 job_id。"""

    document_id: str
    job_id: str
    filename: str
    size: int
    message: str = "文件已接收，正在解析入库"


class ChunkResult(BaseModel):
    """单个检索命中（带可核验引用）。"""

    score: float
    text: str
    source: str
    document_id: str
    chunk_id: str
    citation: str
    original_filename: str = ""
    page: str = ""


class KnowledgeSearchResponse(BaseModel):
    """检索响应。"""

    query: str
    results: list[ChunkResult]


class DocumentItem(BaseModel):
    """文档列表项。"""

    document_id: str
    original_filename: str
    content_type: str
    size: int
    sha256: str
    status: str
    chunk_count: int = 0
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


class DocumentList(BaseModel):
    total: int
    items: list[DocumentItem]


class JobItem(BaseModel):
    """导入任务状态。"""

    job_id: str
    document_id: str
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
