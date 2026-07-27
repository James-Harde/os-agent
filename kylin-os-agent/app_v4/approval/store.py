"""审批存储 — SQLite 实现。

P1 新增：
  - 审批单创建/查询/操作
  - 幂等：重复 approve 不重复执行
  - 每个审批单绑定 run_id + thread_id 用于恢复
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v4.db"


class ApprovalStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create(
        self,
        run_id: str,
        thread_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        risk_level: str = "medium",
        *,
        idempotency_key: str | None = None,
    ) -> str:
        """创建审批单。返回 approval_id。

        §5 Gate 2：审批 ID 必须由稳定幂等键创建。
        若提供 idempotency_key，先查找是否已存在相同键的审批单：
          - 存在则直接返回其 id（恢复时不创建第二个）
          - 不存在则创建，并把 idempotency_key 存入 record
        """
        if idempotency_key:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing["id"]

        approval_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into approvals
                (id, run_id, thread_id, tool_name, arguments_json,
                 reason, risk_level, status, created_at, idempotency_key)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id, run_id, thread_id, tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    reason, risk_level, "pending", now,
                    idempotency_key or None,
                ),
            )
        return approval_id

    def list(self, status_filter: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        """查询审批单列表（按创建时间倒序）。"""
        query = "select * from approvals"
        params: list = []
        if status_filter:
            query += " where status = ?"
            params.append(status_filter)
        query += " order by created_at desc limit ?"
        params.append(max(1, min(limit, 100)))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["arguments"] = json.loads(d.get("arguments_json", "{}"))
            result.append(d)
        return result

    def get(self, approval_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from approvals where id = ?", (approval_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["arguments"] = json.loads(d.get("arguments_json", "{}"))
        return d

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        """按幂等键查找审批单（恢复时复用同一 ID）。"""
        with self._connect() as conn:
            row = conn.execute(
                "select * from approvals where idempotency_key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["arguments"] = json.loads(d.get("arguments_json", "{}"))
        return d

    def approve(self, approval_id: str) -> dict[str, Any] | None:
        """批准（幂等：已处理的返回当前状态，不重复执行）。"""
        record = self.get(approval_id)
        if record is None:
            return None
        if record["status"] != "pending":
            return record  # 幂等：已处理则直接返回
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "update approvals set status = ?, decided_at = ? where id = ?",
                ("approved", now, approval_id),
            )
        return self.get(approval_id)

    def reject(self, approval_id: str) -> dict[str, Any] | None:
        """拒绝（幂等）。"""
        record = self.get(approval_id)
        if record is None:
            return None
        if record["status"] != "pending":
            return record
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "update approvals set status = ?, decided_at = ? where id = ?",
                ("rejected", now, approval_id),
            )
        return self.get(approval_id)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                create table if not exists approvals (
                    id text primary key,
                    run_id text not null,
                    thread_id text not null,
                    tool_name text not null,
                    arguments_json text not null,
                    reason text not null,
                    risk_level text not null,
                    status text not null,
                    created_at text not null,
                    decided_at text,
                    idempotency_key text
                )
            """)
            # 迁移：旧表无 idempotency_key 列时自动添加
            cols = [row[1] for row in conn.execute("pragma table_info(approvals)").fetchall()]
            if "idempotency_key" not in cols:
                conn.execute("alter table approvals add column idempotency_key text")
            conn.execute("""
                create unique index if not exists idx_approvals_idempotency
                on approvals (idempotency_key) where idempotency_key is not null
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


def get_approval_store() -> ApprovalStore:
    """向后兼容入口：路由到当前活动容器。"""
    from app_v4.container import get_deps
    return get_deps().approval_store
