"""LangGraph 检查点持久化。

教学要点：
  旧版手写 SQLite 存 conversations/messages 表，手动做 CRUD。

  LangGraph 提供了 Checkpointer 抽象：
    - 每次节点执行完，框架自动把 state 序列化存起来
    - 不需要你手动写"存消息"的代码
    - 断了可以从任意历史节点恢复

  内置实现：
    - SqliteSaver  — 单机开发（本项目用这个）
    - PostgresSaver — 生产部署
    - 你还可以自定义（Redis、S3 等）

  你只需要在 compile(checkpointer=...) 时传入，框架自动管理。
"""

from __future__ import annotations

import os
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def build_checkpointer() -> SqliteSaver:
    """构建 SQLite 检查点。

    存储位置沿用旧版的 data/audit.db。
    thread_id 就是 conversation_id，每次对话一个独立线程。
    """
    db_path = str(Path(__file__).resolve().parents[2] / "data" / "agent_v2.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)
