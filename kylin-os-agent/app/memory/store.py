from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import DB_PATH


class MemoryStore:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def ensure_conversation(self, conversation_id: str | None, title: str) -> str:
        now = self._now()
        cid = conversation_id or str(uuid.uuid4())
        with self._connect() as conn:
            row = conn.execute(
                "select id from conversations where id = ?",
                (cid,),
            ).fetchone()
            if row:
                conn.execute(
                    "update conversations set updated_at = ? where id = ?",
                    (now, cid),
                )
            else:
                conn.execute(
                    """
                    insert into conversations (id, title, created_at, updated_at)
                    values (?, ?, ?, ?)
                    """,
                    (cid, title[:80] or "New conversation", now, now),
                )
        return cid

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into messages (
                    conversation_id, role, content, metadata_json, created_at
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    role,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    self._now(),
                ),
            )
            conn.execute(
                "update conversations set updated_at = ? where id = ?",
                (self._now(), conversation_id),
            )

    def recent_messages(self, conversation_id: str, limit: int = 8) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 30))
        with self._connect() as conn:
            rows = conn.execute(
                """
                select role, content, metadata_json, created_at
                from messages
                where conversation_id = ?
                order by id desc
                limit ?
                """,
                (conversation_id, bounded_limit),
            ).fetchall()
        rows = list(reversed(rows))
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_conversations(self, limit: int = 20) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, title, created_at, updated_at
                from conversations
                order by updated_at desc
                limit ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists conversations (
                    id text primary key,
                    title text not null,
                    created_at text not null,
                    updated_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists messages (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    role text not null,
                    content text not null,
                    metadata_json text not null,
                    created_at text not null,
                    foreign key (conversation_id) references conversations(id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

