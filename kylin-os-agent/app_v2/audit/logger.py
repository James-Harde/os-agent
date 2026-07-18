"""审计日志 — 保留旧版 SQLite 逻辑不变。

教学要点：
  "审计"是业务需求，不是框架能力。
  LangChain/LangGraph 不提供审计日志方案，这需要你自己写。
  所以 audit/logger.py 基本原样保留。

  唯一变化：不再依赖 ToolRegistry 回调，而是在图的执行之后手动调用。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v2.db"


class AuditLogger:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def record(self, conversation_id: str, result: dict[str, Any]) -> None:
        """记录一次完整的 agent 调用结果。"""
        with self._connect() as conn:
            conn.execute(
                """
                insert into audit_logs
                (conversation_id, intent, guard_decision, tool_calls_json,
                 answer, answer_source, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    result.get("intent", ""),
                    result.get("guard_decision", ""),
                    json.dumps(result.get("tool_calls", []), ensure_ascii=False),
                    result.get("answer", ""),
                    result.get("answer_source", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def list_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select * from audit_logs order by id desc limit ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                create table if not exists audit_logs (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    intent text not null,
                    guard_decision text not null,
                    tool_calls_json text not null,
                    answer text not null,
                    answer_source text not null,
                    created_at text not null
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
