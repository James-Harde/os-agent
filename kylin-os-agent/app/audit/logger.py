from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.config import DB_PATH


class AuditLogger:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def record_tool_call(
        self,
        request_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        risk_level: str,
        status: str,
        reason: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into tool_calls (
                    request_id, tool_name, arguments_json, result_json,
                    risk_level, status, reason, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    risk_level,
                    status,
                    reason,
                    self._now(),
                ),
            )

    def record_audit_log(
        self,
        request_id: str,
        user_input: str,
        intent: str,
        plan: list[dict[str, Any]],
        risk_level: str,
        guard_decision: str,
        final_answer: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into audit_logs (
                    request_id, user_input, intent, plan_json, risk_level,
                    guard_decision, final_answer, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    user_input,
                    intent,
                    json.dumps(plan, ensure_ascii=False),
                    risk_level,
                    guard_decision,
                    final_answer,
                    self._now(),
                ),
            )

    def list_audit_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                select request_id, user_input, intent, plan_json, risk_level,
                       guard_decision, final_answer, created_at
                from audit_logs
                order by id desc
                limit ?
                """,
                (bounded_limit,),
            ).fetchall()

        return [
            {
                "request_id": row["request_id"],
                "user_input": row["user_input"],
                "intent": row["intent"],
                "plan": json.loads(row["plan_json"]),
                "risk_level": row["risk_level"],
                "guard_decision": row["guard_decision"],
                "final_answer": row["final_answer"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists tool_calls (
                    id integer primary key autoincrement,
                    request_id text not null,
                    tool_name text not null,
                    arguments_json text not null,
                    result_json text not null,
                    risk_level text not null,
                    status text not null,
                    reason text not null,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists audit_logs (
                    id integer primary key autoincrement,
                    request_id text not null,
                    user_input text not null,
                    intent text not null,
                    plan_json text not null,
                    risk_level text not null,
                    guard_decision text not null,
                    final_answer text not null,
                    created_at text not null
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

