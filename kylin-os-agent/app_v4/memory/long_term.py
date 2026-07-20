"""长期记忆层 — SQLite 跨 thread 结论/画像存储。

职责分离：
  - 短期记忆（thread 内多轮）：由 LangGraph checkpointer（SqliteSaver）负责，无需重做。
  - 长期记忆（跨 thread 结论 + 用户画像）：本类负责。

设计要点：
  - 与 audit/checkpointer 共用同一个 data/agent_v4.db，但使用独立表 long_term_memories。
  - `kind="conclusion"`：每次 run 结束时保存的结论摘要，可跨 thread 召回（最近 N 条）。
  - `kind="profile"`：用户画像键值，按 thread_id 维度累积（如常问领域、偏好风格）。
  - recall() 返回结构化字典，供 plan_node 注入 system prompt，实现"记忆影响规划"。
  - 全部 SQLite 操作使用 check_same_thread=False，兼容 LangGraph checkpointer 的连接模式。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v4.db"


class LongTermMemory:
    """SQLite 长期记忆存储。"""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def save_conclusion(self, thread_id: str, run_id: str, intent: str, summary: str) -> int:
        """保存一条结论摘要。返回 row id。"""
        return self._insert(
            thread_id=thread_id,
            run_id=run_id,
            kind="conclusion",
            key=intent,
            value=summary[:500],
        )

    def save_profile(self, thread_id: str, key: str, value: str) -> int:
        """保存/覆盖一个画像键值（同一 key 保留多条，recall 取最新）。"""
        return self._insert(
            thread_id=thread_id,
            run_id="",
            kind="profile",
            key=key,
            value=value[:200],
        )

    def recall(self, thread_id: str, limit: int = 5) -> dict[str, Any]:
        """召回结构：近期结论 + 用户画像。

        返回:
          {
            "conclusions": [{"intent":..., "summary":..., "created_at":...}],
            "profile": {key: value, ...}   # 每个 key 取最新值
          }
        """
        conclusions = self._recent_conclusions(thread_id, limit=limit)
        profile = self._latest_profile(thread_id)
        return {"conclusions": conclusions, "profile": profile}

    def record(self, thread_id: str, run_id: str, intent: str, answer: str, answer_source: str) -> None:
        """run 结束后的统一写入入口。

        - 总是保存一条结论摘要。
        - 画像：仅从 intent 维度累积（标记"常问领域"），轻量可持续。
        """
        # 仅对真实生成的回答保存结论；拒绝模板不产生有效结论
        if answer_source in ("llm_summary", "empty_plan_template", "rag_summary"):
            self.save_conclusion(thread_id, run_id, intent, answer)
        # 画像累积：记录 intent 出现（用计数形式便于后续扩展）
        self.save_profile(thread_id, f"intent:{intent}", "1")

    # ------------------------------------------------------------------
    # 内部查询
    # ------------------------------------------------------------------
    def _recent_conclusions(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select key, value, created_at
                from long_term_memories
                where thread_id = ? and kind = 'conclusion'
                order by id desc
                limit ?
                """,
                (thread_id, max(1, min(limit, 50))),
            ).fetchall()
        return [{"intent": r[0], "summary": r[1], "created_at": r[2]} for r in rows]

    def _latest_profile(self, thread_id: str) -> dict[str, str]:
        """每个 profile key 取最新值（子查询 max(id) 去重）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                select t.key, t.value
                from long_term_memories t
                inner join (
                    select key, max(id) as max_id
                    from long_term_memories
                    where thread_id = ? and kind = 'profile'
                    group by key
                ) m on t.key = m.key and t.id = m.max_id
                """,
                (thread_id,),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def _insert(self, thread_id: str, run_id: str, kind: str, key: str, value: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                insert into long_term_memories
                (thread_id, run_id, kind, key, value, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (thread_id, run_id, kind, key, value,
                 datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                create table if not exists long_term_memories (
                    id integer primary key autoincrement,
                    thread_id text not null,
                    run_id text not null,
                    kind text not null,
                    key text not null,
                    value text not null,
                    created_at text not null
                )
            """)
            conn.execute("""
                create index if not exists idx_ltm_thread_kind
                on long_term_memories (thread_id, kind)
            """)


# 单例
_memory: LongTermMemory | None = None


def get_long_term_memory() -> LongTermMemory:
    global _memory
    if _memory is None:
        _memory = LongTermMemory()
    return _memory
