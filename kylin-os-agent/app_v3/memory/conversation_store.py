"""LangChain 版记忆 —— 手写 SQLite（对应 LangGraph 的 Checkpointer）。

教学要点：
  LangChain AgentExecutor 没有内置的状态持久化机制。
  它接受一个 messages 参数来"记住历史"，但：
    - 存消息、取消息、对话管理 —— 都要你自己写

  这和 v2 形成鲜明对比：
    v2 (LangGraph): compile(checkpointer=SqliteSaver()) → 框架自动存/取
    v3 (LangChain): 手写 ConversationStore → 手动管理消息生命周期

  这是 LangChain AgentExecutor 被诟病"不适合生产"的核心原因之一 ——
  你不得不用大量手写代码来解决框架不管的问题。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v3.db"


class ConversationStore:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def ensure_conversation(self, cid: str | None, title: str) -> str:
        cid = cid or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if conn.execute("select id from conversations where id=?", (cid,)).fetchone():
                conn.execute("update conversations set updated_at=? where id=?", (now, cid))
            else:
                conn.execute(
                    "insert into conversations (id,title,created_at,updated_at) values (?,?,?,?)",
                    (cid, title[:80] or "新对话", now, now),
                )
        return cid

    def add_message(self, cid: str, role: str, content: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "insert into messages (conversation_id,role,content,created_at) values (?,?,?,?)",
                (cid, role, content, now),
            )
            conn.execute("update conversations set updated_at=? where id=?", (now, cid))

    def recent_messages(self, cid: str, limit: int = 8) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select role,content from messages where conversation_id=? order by id desc limit ?",
                (cid, max(1, min(limit, 30))),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                create table if not exists conversations (
                    id text primary key, title text not null,
                    created_at text not null, updated_at text not null
                )""")
            conn.execute("""
                create table if not exists messages (
                    id integer primary key autoincrement,
                    conversation_id text not null, role text not null,
                    content text not null, created_at text not null
                )""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
