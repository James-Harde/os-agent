"""SQLite 文档元数据 + 任务状态清单。

持久化两类状态（均不含文档完整文本 / 二进制 / 密钥）：
  - documents : 上传文件元数据（document_id / 原文件名 / 存储名 / 类型 / 大小 /
                SHA-256 / 状态 / chunk_count / 错误）。
  - jobs      : 导入任务状态（queued / parsing / embedding / indexed / failed +
                进度 / 错误）。

仅供知识库模块内部使用；测试可注入临时路径。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_document ON jobs(document_id);
"""


class DocumentMetadataStore:
    """线程安全的 SQLite 元数据清单。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(SCHEMA)

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    def insert_document(
        self,
        *,
        document_id: str,
        original_filename: str,
        stored_filename: str,
        content_type: str,
        size: int,
        sha256: str,
        status: str = "queued",
    ) -> None:
        now = _now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO documents "
                    "(document_id, original_filename, stored_filename, content_type, "
                    "size, sha256, status, chunk_count, error, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (document_id, original_filename, stored_filename, content_type,
                     size, sha256, status, None, now, now),
                )

    def update_document_status(
        self, document_id: str, *, status: str, chunk_count: int | None = None, error: str | None = None,
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                if chunk_count is not None:
                    conn.execute(
                        "UPDATE documents SET status=?, chunk_count=?, error=?, updated_at=? "
                        "WHERE document_id=?",
                        (status, chunk_count, error, _now(), document_id),
                    )
                else:
                    conn.execute(
                        "UPDATE documents SET status=?, error=?, updated_at=? WHERE document_id=?",
                        (status, error, _now(), document_id),
                    )

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM documents WHERE document_id=?", (document_id,)
                ).fetchone()
        return dict(row) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM documents ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, document_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
                return cur.rowcount > 0

    def total_bytes(self) -> int:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COALESCE(SUM(size), 0) AS total FROM documents").fetchone()
        return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    def insert_job(self, *, job_id: str, document_id: str, status: str = "queued") -> None:
        import json
        now = _now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO jobs (job_id, document_id, status, progress, error, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job_id, document_id, status, "{}", None, now, now),
                )

    def update_job(
        self, job_id: str, *, status: str, progress: dict[str, Any] | None = None, error: str | None = None,
    ) -> None:
        import json
        with self._lock:
            with self._connect() as conn:
                prog_json = json.dumps(progress or {}, ensure_ascii=False)
                conn.execute(
                    "UPDATE jobs SET status=?, progress=?, error=?, updated_at=? WHERE job_id=?",
                    (status, prog_json, error, _now(), job_id),
                )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        import json
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["progress"] = json.loads(d.get("progress") or "{}")
        except Exception:
            d["progress"] = {}
        return d
