"""审计日志 —— 和 v2 一样手写的 SQLite 实现。

教学要点：
  "审计"是业务需求，LangChain 和 LangGraph 都不提供这层，必须自己写。
  所以三个版本的 audit/logger.py 基本同构。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v3.db"


class AuditLogger:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def record(self, conversation_id: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into audit_logs
                (conversation_id, intent, guard_decision, answer, answer_source, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    result.get("intent", ""),
                    result.get("guard_decision", ""),
                    result.get("answer", ""),
                    result.get("answer_source", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                create table if not exists audit_logs (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    intent text not null,
                    guard_decision text not null,
                    answer text not null,
                    answer_source text not null,
                    created_at text not null
                )""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
