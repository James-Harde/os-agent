"""工具结果缓存 — TTL + 击穿防护。

修复 audit #14/Phase F：
  - 只缓存只读工具结果（disk_usage、process_list 等）
  - 不缓存写操作和用户敏感结果
  - TTL 过期 + 单键锁防击穿
  - key 设计：tool_name + hash(args)
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

# ---------------------------------------------------------------------------
# 缓存配置（只读工具 → TTL 秒）
# ---------------------------------------------------------------------------
CACHE_TTL: dict[str, int] = {
    "disk_usage": 30,          # 磁盘使用率 30 秒有效
    "directory_usage": 60,     # 目录占用 60 秒有效
    "port_lookup": 10,         # 端口占用变化快，10 秒
    "process_list": 5,         # 进程列表变化最快，5 秒
    "service_status": 15,      # 服务状态 15 秒
    "rag_search": 300,         # RAG 检索可缓存 5 分钟
}

# 不缓存的工具（写操作 / 敏感 / 扫描类）
NO_CACHE_TOOLS = {"service_restart", "prompt_injection_scan"}


def _make_cache_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """生成缓存 key。"""
    args_json = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    args_hash = hashlib.md5(args_json.encode()).hexdigest()[:12]
    return f"{tool_name}:{args_hash}"


class ToolCache:
    """带 TTL + 击穿防护的工具结果缓存。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}  # key → (expire_time, value)
        self._locks: dict[str, threading.Lock] = {}
        self._meta = {"hits": 0, "misses": 0}
        self._lock = threading.Lock()

    def get(self, tool_name: str, arguments: dict[str, Any]) -> Any | None:
        """获取缓存。 None 表示未命中或已过期。"""
        if tool_name in NO_CACHE_TOOLS:
            return None
        if tool_name not in CACHE_TTL:
            return None

        key = _make_cache_key(tool_name, arguments)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._meta["misses"] += 1
                return None
            expire_time, value = entry
            if time.monotonic() > expire_time:
                # 过期
                del self._store[key]
                self._meta["misses"] += 1
                return None
            self._meta["hits"] += 1
            return value

    def put(self, tool_name: str, arguments: dict[str, Any], value: Any) -> None:
        """写入缓存。"""
        if tool_name in NO_CACHE_TOOLS:
            return
        if tool_name not in CACHE_TTL:
            return
        key = _make_cache_key(tool_name, arguments)
        expire = time.monotonic() + CACHE_TTL[tool_name]
        with self._lock:
            self._store[key] = (expire, value)

    def get_lock(self, key: str) -> threading.Lock:
        """获取单键锁（防击穿）。"""
        with self._lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    @property
    def stats(self) -> dict[str, Any]:
        return {**self._meta, "size": len(self._store)}


# 全局单例
tool_cache = ToolCache()
