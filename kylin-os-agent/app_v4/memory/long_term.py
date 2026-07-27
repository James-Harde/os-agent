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
    def save_conclusion(self, thread_id: str, run_id: str, intent: str, summary: str, user_id: str = "") -> int:
        """保存一条结论摘要。返回 row id。"""
        return self._insert(
            thread_id=thread_id,
            run_id=run_id,
            kind="conclusion",
            key=intent,
            value=summary[:500],
            user_id=user_id,
        )

    def save_profile(self, thread_id: str, key: str, value: str, user_id: str = "") -> int:
        """保存/覆盖一个画像键值（同一 key 保留多条，recall 取最新）。"""
        return self._insert(
            thread_id=thread_id,
            run_id="",
            kind="profile",
            key=key,
            value=value[:200],
            user_id=user_id,
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

    # ------------------------------------------------------------------
    # Phase G：跨 thread 检索 + 过期/删除
    # ------------------------------------------------------------------
    def recall_cross_thread(self, user_id: str, limit: int = 10) -> dict[str, Any]:
        """跨 thread 召回同一用户的记忆（修复 audit #13）。

        通过 user_id 关联多个 thread，实现跨会话记忆。
        """
        conclusions = self._recent_conclusions_by_user(user_id, limit=limit)
        profile = self._latest_profile_by_user(user_id)
        return {"conclusions": conclusions, "profile": profile}

    def recall_with_expiry(
        self, thread_id: str, limit: int = 5, max_age_hours: int | None = None,
    ) -> dict[str, Any]:
        """支持过期的召回（Phase G：记忆 TTL）。

        max_age_hours: 只返回创建时间在此范围内的记忆，None 表示不过期。
        """
        conclusions = self._recent_conclusions(thread_id, limit=limit)
        if max_age_hours is not None:
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            cutoff_str = cutoff.isoformat()
            conclusions = [c for c in conclusions if c.get("created_at", "") >= cutoff_str]
        profile = self._latest_profile(thread_id)
        return {"conclusions": conclusions, "profile": profile}

    def delete_memory(self, memory_id: int) -> bool:
        """删除指定记忆（Phase G：用户删除权）。"""
        with self._connect() as conn:
            cur = conn.execute("delete from long_term_memories where id = ?", (memory_id,))
            return cur.rowcount > 0

    def delete_all_by_thread(self, thread_id: str) -> int:
        """删除指定 thread 的所有记忆（Phase G：数据清理）。"""
        with self._connect() as conn:
            cur = conn.execute("delete from long_term_memories where thread_id = ?", (thread_id,))
            return cur.rowcount

    def correct_memory(self, memory_id: int, new_value: str) -> bool:
        """纠正（覆盖）指定记忆的值（Phase G：用户纠错）。"""
        with self._connect() as conn:
            cur = conn.execute(
                "update long_term_memories set value = ? where id = ?",
                (new_value[:500], memory_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Phase G：记忆压缩 + 污染防护
    # ------------------------------------------------------------------
    def compress_conclusions(self, thread_id: str, keep_latest: int = 5) -> int:
        """压缩旧结论：只保留最新 N 条，删除更早的（Phase G：记忆压缩）。

        返回删除的条数。
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                delete from long_term_memories
                where thread_id = ? and kind = 'conclusion'
                and id not in (
                    select id from long_term_memories
                    where thread_id = ? and kind = 'conclusion'
                    order by id desc limit ?
                )
                """,
                (thread_id, thread_id, keep_latest),
            )
            return cur.rowcount

    def detect_pollution(self, thread_id: str) -> list[dict[str, Any]]:
        """检测潜在记忆污染（Phase G：污染防护）。

        检测规则：
          - 同一 intent 的结论连续 3 条以上相同（可能是重复注入）
          - 结论包含高危关键词（可能是注入内容）

        返回可疑记忆列表。
        """
        suspicious = []
        with self._connect() as conn:
            # 检测重复 intent（连续 3 条以上）
            rows = conn.execute(
                """
                select key, value, COUNT(*) as cnt
                from long_term_memories
                where thread_id = ? and kind = 'conclusion'
                group by key, value
                having cnt >= 3
                order by cnt desc
                """,
                (thread_id,),
            ).fetchall()
            for r in rows:
                suspicious.append({
                    "type": "repetitive",
                    "key": r[0],
                    "value": r[1][:80],
                    "count": r[2],
                })
        return suspicious

    def get_stats(self, thread_id: str) -> dict[str, Any]:
        """获取记忆统计（用于监控压缩时机）。"""
        with self._connect() as conn:
            total = conn.execute(
                "select count(*) from long_term_memories where thread_id = ?",
                (thread_id,),
            ).fetchone()[0]
            conclusions = conn.execute(
                "select count(*) from long_term_memories where thread_id = ? and kind = 'conclusion'",
                (thread_id,),
            ).fetchone()[0]
            profiles = conn.execute(
                "select count(*) from long_term_memories where thread_id = ? and kind = 'profile'",
                (thread_id,),
            ).fetchone()[0]
        return {"total": total, "conclusions": conclusions, "profiles": profiles}

    def record(
        self, thread_id: str, run_id: str, intent: str, answer: str, answer_source: str,
        user_id: str = "",
    ) -> None:
        """run 结束后的统一写入入口。

        - 总是保存一条结论摘要。
        - 画像：仅从 intent 维度累积（标记"常问领域"），轻量可持续。
        - user_id：非空时写入记忆，使跨 thread 召回（按 user_id）可用。
        """
        # 仅对真实生成的回答保存结论；拒绝模板不产生有效结论。
        # model_final_answer：ReAct 循环中模型给出的 final answer（基于 Observation
        # 生成的结论），属于有效结论，需写入记忆以支持跨 thread 召回。
        if answer_source in ("llm_summary", "empty_plan_template", "rag_summary",
                             "readonly_react_summary", "direct_answer",
                             "model_final_answer"):
            self.save_conclusion(thread_id, run_id, intent, answer, user_id=user_id)
        # 画像累积：记录 intent 出现（用计数形式便于后续扩展）
        self.save_profile(thread_id, f"intent:{intent}", "1", user_id=user_id)

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

    # ------------------------------------------------------------------
    # Phase G：跨 thread 查询
    # ------------------------------------------------------------------
    def _recent_conclusions_by_user(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """按 user_id 跨 thread 召回结论。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                select key, value, created_at
                from long_term_memories
                where user_id = ? and kind = 'conclusion'
                order by id desc
                limit ?
                """,
                (user_id, max(1, min(limit, 50))),
            ).fetchall()
        return [{"intent": r[0], "summary": r[1], "created_at": r[2]} for r in rows]

    def _latest_profile_by_user(self, user_id: str) -> dict[str, str]:
        """按 user_id 跨 thread 取最新画像。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                select t.key, t.value
                from long_term_memories t
                inner join (
                    select key, max(id) as max_id
                    from long_term_memories
                    where user_id = ? and kind = 'profile'
                    group by key
                ) m on t.key = m.key and t.id = m.max_id
                """,
                (user_id,),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def _insert(
        self,
        thread_id: str,
        run_id: str,
        kind: str,
        key: str,
        value: str,
        user_id: str = "",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                insert into long_term_memories
                (thread_id, run_id, kind, key, value, user_id, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (thread_id, run_id, kind, key, value, user_id,
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
                    user_id text not null default '',
                    created_at text not null
                )
            """)
            # 迁移：旧表无 user_id 列时自动添加（ALTER TABLE ADD COLUMN）
            cols = [row[1] for row in conn.execute("pragma table_info(long_term_memories)").fetchall()]
            if "user_id" not in cols:
                conn.execute("alter table long_term_memories add column user_id text not null default ''")
            conn.execute("""
                create index if not exists idx_ltm_thread_kind
                on long_term_memories (thread_id, kind)
            """)
            conn.execute("""
                create index if not exists idx_ltm_user_kind
                on long_term_memories (user_id, kind)
            """)


# 向后兼容入口：路由到当前活动容器
def get_long_term_memory() -> LongTermMemory:
    from app_v4.container import get_deps
    return get_deps().long_term_memory
