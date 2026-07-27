"""LangGraph 检查点持久化。

提供两种模式：
  - build_checkpointer()      → 同步 SqliteSaver（供 graph.invoke 使用）
  - build_async_checkpointer() → 异步 AsyncSqliteSaver（供 graph.astream 使用）

注意：AsyncSqliteSaver 必须在运行中的事件循环里构造（它内部调用
asyncio.get_running_loop()），所以 build_async_checkpointer 是 async 函数。
"""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_v4.db"


def build_checkpointer(db_path: str | Path | None = None) -> SqliteSaver:
    """构建同步 SQLite 检查点。"""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn)


async def build_async_checkpointer(db_path: str | Path | None = None) -> AsyncSqliteSaver:
    """构建异步 SQLite 检查点（必须在运行中的事件循环里调用）。"""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    return AsyncSqliteSaver(conn)
