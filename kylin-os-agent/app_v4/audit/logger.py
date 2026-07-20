"""审计日志 — SQLite 存储。

对比 app_v2 改动：
  - 新增 run_id 字段
  - 新增 record_trace 方法记录完整 Trace
  - 提供 get_trace_by_run_id 查询单次 Run 的 Trace
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v4.db"


class AuditLogger:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def record(self, conversation_id: str, result: dict[str, Any]) -> None:
        """记录一次完整调用结果。"""
        with self._connect() as conn:
            conn.execute(
                """
                insert into audit_logs
                (run_id, conversation_id, intent, guard_decision, tool_calls_json,
                 answer, answer_source, trace_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.get("run_id", ""),
                    conversation_id,
                    result.get("intent", ""),
                    result.get("guard_decision", ""),
                    json.dumps(result.get("tool_calls", []), ensure_ascii=False),
                    result.get("answer", ""),
                    result.get("answer_source", ""),
                    json.dumps(result.get("trace_steps", []), ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_trace(self, run_id: str) -> dict[str, Any] | None:
        """按 run_id 查询单次 Run 的完整记录。"""
        with self._connect() as conn:
            row = conn.execute(
                "select * from audit_logs where run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tool_calls"] = json.loads(d.get("tool_calls_json", "[]"))
        d["trace_steps"] = json.loads(d.get("trace_json", "[]"))
        return d

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
                    run_id text not null,
                    conversation_id text not null,
                    intent text not null,
                    guard_decision text not null,
                    tool_calls_json text not null,
                    answer text not null,
                    answer_source text not null,
                    trace_json text not null,
                    created_at text not null
                )
            """)
            conn.execute("""
                create index if not exists idx_audit_run_id
                on audit_logs (run_id)
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


# 单例
_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    global _logger
    if _logger is None:
        _logger = AuditLogger()
    return _logger
