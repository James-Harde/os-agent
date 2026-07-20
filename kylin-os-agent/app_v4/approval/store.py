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

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v4.db"


class ApprovalStore:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create(
        self,
        run_id: str,
        thread_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        risk_level: str = "medium",
    ) -> str:
        """创建审批单。返回 approval_id。"""
        approval_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into approvals
                (id, run_id, thread_id, tool_name, arguments_json,
                 reason, risk_level, status, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id, run_id, thread_id, tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    reason, risk_level, "pending", now,
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
                    decided_at text
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


_store: ApprovalStore | None = None


def get_approval_store() -> ApprovalStore:
    global _store
    if _store is None:
        _store = ApprovalStore()
    return _store
