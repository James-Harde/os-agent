"""ApprovalRequest service — SQLite-backed pending-approval lifecycle.

Three statuses: pending → approved | rejected.
When an approval is approved, the caller (orchestrator) is responsible for
actually executing the tool through ToolRegistry.call() again.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import DB_PATH


class ApprovalService:
    """Manage approval requests for confirm-class tools."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        request_id: str,
        conversation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        requested_by: str = "agent",
    ) -> str:
        """Create a pending approval row and return its ID."""
        approval_id = str(uuid.uuid4())
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                insert into approval_requests (
                    id, request_id, conversation_id, tool_name,
                    arguments_json, status, requested_by, requested_at
                ) values (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id,
                    request_id,
                    conversation_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    requested_by,
                    now,
                ),
            )
        return approval_id

    def list_all(self, limit: int = 30) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, request_id, conversation_id, tool_name,
                       arguments_json, status, requested_by, requested_at,
                       decided_at, decided_by, justification, denial_reason
                from approval_requests
                order by id desc
                limit ?
                """,
                (bounded,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def list_pending(self, limit: int = 30) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, request_id, conversation_id, tool_name,
                       arguments_json, status, requested_by, requested_at,
                       decided_at, decided_by, justification, denial_reason
                from approval_requests
                where status = 'pending'
                order by id desc
                limit ?
                """,
                (bounded,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def decide(
        self,
        approval_id: str,
        decided_by: str,
        approve: bool,
        reason: str = "",
    ) -> dict[str, Any] | None:
        """Approve or reject a pending request.

        Returns the new approval record, or None if the id is not found
        or the request is no longer pending.
        """
        now = self._now()
        new_status = "approved" if approve else "rejected"
        column = "justification" if approve else "denial_reason"
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                update approval_requests
                set status = ?, decided_at = ?, decided_by = ?, {column} = ?
                where id = ? and status = 'pending'
                """,
                (new_status, now, decided_by, reason, approval_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "select * from approval_requests where id = ?",
                (approval_id,),
            ).fetchone()
        return self._decode_row(row)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists approval_requests (
                    id text primary key,
                    request_id text not null,
                    conversation_id text not null,
                    tool_name text not null,
                    arguments_json text not null,
                    status text not null check(status in ('pending','approved','rejected')),
                    requested_by text not null,
                    requested_at text not null,
                    decided_at text,
                    decided_by text,
                    justification text,
                    denial_reason text
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        base = dict(row)
        base["arguments"] = json.loads(base.pop("arguments_json"))
        return base
