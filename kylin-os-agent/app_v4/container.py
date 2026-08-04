"""依赖注入容器。

目的：
  - 把全局单例（DB 连接、checkpointer、audit logger、model、cache、限流器、时钟）
    收拢到一个 Dependencies 对象里。
  - 生产代码使用默认容器；测试通过 contextvars 注入临时隔离实例，
    不依赖 data/agent_v4.db，也不污染其他测试。
  - 符合任务规范 §4.1：create_app(settings, dependencies) 工厂 + 可注入边界。

使用：
  deps = build_dependencies(settings)       # 构建
  token = set_deps(deps)                    # 激活（返回 reset token）
  reset_deps(token)                         # 恢复
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app_v4.settings import Settings


# ---------------------------------------------------------------------------
# 可注入外部边界（Protocol）—— 测试可替换这些
# ---------------------------------------------------------------------------
class Clock(Protocol):
    """时间边界（测试可注入确定性时钟）。"""
    def monotonic(self) -> float: ...
    def now_iso(self) -> str: ...


class Model(Protocol):
    """模型边界（测试可注入 scripted model）。"""
    def invoke(self, messages: list[Any]) -> Any: ...


class ToolCacheProtocol(Protocol):
    def get(self, tool_name: str, arguments: dict[str, Any]) -> Any | None: ...
    def put(self, tool_name: str, arguments: dict[str, Any], value: Any) -> None: ...
    def get_lock(self, key: str) -> Any: ...


class LimiterProtocol(Protocol):
    def allow(self, key: str) -> tuple[bool, dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# 真实实现
# ---------------------------------------------------------------------------
class _RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 容器本身
# ---------------------------------------------------------------------------
@dataclass
class Dependencies:
    """所有可注入依赖的聚合。"""

    settings: Settings

    # --- 由 build_dependencies 填充的延迟组件 ---
    db_path: Path = field(default_factory=Path)

    # 延迟初始化标记 + 缓存
    _checkpointer: Any = field(default=None, repr=False)
    _async_checkpointer: Any = field(default=None, repr=False)
    _audit_logger: Any = field(default=None, repr=False)
    _approval_store: Any = field(default=None, repr=False)
    _long_term_memory: Any = field(default=None, repr=False)
    _model: Model | None = field(default=None, repr=False)
    _cache: ToolCacheProtocol | None = field(default=None, repr=False)
    _limiter: LimiterProtocol | None = field(default=None, repr=False)
    _clock: Clock | None = field(default=None, repr=False)
    _graph: Any = field(default=None, repr=False)
    _graph_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _ainvoke_lock: asyncio.Lock | None = field(default=None, repr=False)
    _tool_app_service: Any = field(default=None, repr=False)
    _mcp_invoker: Any = field(default=None, repr=False)
    _rag_store: Any = field(default=None, repr=False)

    def reset(self) -> None:
        """清空延迟组件引用（用于每个测试后隔离）。

        语义：
          - 同步 SqliteSaver 持有 ``sqlite3`` 连接，这里同步关闭后清空引用。
          - 异步 ``AsyncSqliteSaver`` 的 ``aiosqlite`` 连接属于异步资源，同步
            ``reset`` 既无法保证 await 完成，也不能 fire-and-forget 调度后立即
            丢引用（会丢出仍在线的 worker 线程）。因此这里仅清空引用，把关闭
            责任完全交给 ``aclose()``——生产路径通过 FastAPI lifespan 调用它，
            测试路径通过 ``with TestClient(app)`` 触发 lifespan，或在容器生命周期
            末尾显式 ``await deps.aclose()``。
          - 禁止守护线程、轮询、sleep，也不在同步方法里吞掉异步资源错误。
        """
        if self._checkpointer is not None:
            # 同步 SqliteSaver 持有 sqlite3 连接，同步关闭。
            with contextlib.suppress(Exception):
                self._checkpointer.conn.close()
        # 异步 checkpointer 只清空引用；关闭由 aclose() 在异步上下文里完成。
        self._checkpointer = None
        self._async_checkpointer = None
        self._audit_logger = None
        self._approval_store = None
        self._long_term_memory = None
        self._model = None
        self._cache = None
        self._limiter = None
        self._clock = None
        self._graph = None
        self._ainvoke_lock = None
        self._tool_app_service = None
        self._mcp_invoker = None
        self._rag_store = None

    async def aclose(self) -> None:
        """幂等异步关闭：关闭容器持有的所有异步资源。

        生产路径通过 FastAPI lifespan 调用本方法，确保 AsyncSqliteSaver 的
        aiosqlite 连接（及其 worker 线程）在事件循环关闭前被显式关闭，
        避免 ``PytestUnhandledThreadExceptionWarning``。

        关闭对象：
          - AsyncSqliteSaver 的 aiosqlite 连接
          - 同步 SqliteSaver 的 sqlite3 连接
          - 其他确实由容器持有且提供 ``close``/``aclose`` 的异步资源

        重复调用不得报错：每次关闭前检查引用是否为 None，已关闭的资源直接跳过。
        """
        # --- AsyncSqliteSaver / aiosqlite ---
        if self._async_checkpointer is not None:
            conn = getattr(self._async_checkpointer, "conn", None)
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
            self._async_checkpointer = None
        # --- 同步 SqliteSaver / sqlite3 ---
        if self._checkpointer is not None:
            with contextlib.suppress(Exception):
                self._checkpointer.conn.close()
            self._checkpointer = None
        # --- 模型（ChatOpenAI 等）---
        if self._model is not None:
            aclose = getattr(self._model, "aclose", None)
            if callable(aclose):
                with contextlib.suppress(Exception):
                    await aclose()
            self._model = None
        # --- MCP invoker（若持有长连接）---
        if self._mcp_invoker is not None:
            aclose = getattr(self._mcp_invoker, "aclose", None)
            if callable(aclose):
                with contextlib.suppress(Exception):
                    await aclose()
            self._mcp_invoker = None
        # --- RAG store（Milvus 等，若持有连接）---
        if self._rag_store is not None:
            for name in ("aclose", "close"):
                fn = getattr(self._rag_store, name, None)
                if callable(fn):
                    with contextlib.suppress(Exception):
                        await fn() if name == "aclose" else fn()
                    break
            self._rag_store = None
        # 其余组件（audit/approval/memory/cache/limiter/clock/graph）不持有
        # 需要显式关闭的持久连接，直接清空引用。
        self._audit_logger = None
        self._approval_store = None
        self._long_term_memory = None
        self._cache = None
        self._limiter = None
        self._clock = None
        self._graph = None
        self._ainvoke_lock = None
        self._tool_app_service = None

    # ------------------------------------------------------------------
    # 延迟构建的组件（每个组件在首次访问时按当前 settings 创建）
    # ------------------------------------------------------------------
    @property
    def clock(self) -> Clock:
        if self._clock is None:
            self._clock = _RealClock()
        return self._clock

    @property
    def audit_logger(self):
        if self._audit_logger is None:
            from app_v4.audit.logger import AuditLogger
            self._audit_logger = AuditLogger(db_path=str(self.db_path))
        return self._audit_logger

    @property
    def approval_store(self):
        if self._approval_store is None:
            from app_v4.approval.store import ApprovalStore
            self._approval_store = ApprovalStore(db_path=str(self.db_path))
        return self._approval_store

    @property
    def long_term_memory(self):
        if self._long_term_memory is None:
            from app_v4.memory.long_term import LongTermMemory
            self._long_term_memory = LongTermMemory(db_path=str(self.db_path))
        return self._long_term_memory

    @property
    def cache(self) -> ToolCacheProtocol:
        if self._cache is None:
            from app_v4.graph.tool_cache import ToolCache
            self._cache = ToolCache()
        return self._cache

    @property
    def limiter(self) -> LimiterProtocol:
        if self._limiter is None:
            from app_v4.graph.rate_limiter import TokenBucketRateLimiter
            s = self.settings
            self._limiter = TokenBucketRateLimiter(
                capacity=s.rate_limit_capacity,
                refill_rate=s.rate_limit_refill_rate,
            )
        return self._limiter

    @property
    def model(self) -> Model:
        if self._model is None:
            from app_v4.model.chat_model import build_chat_model
            self._model = build_chat_model(self.settings)
        return self._model

    @property
    def tool_app_service(self):
        """统一工具应用服务（§4.4 #1）。

        B6：按 settings.mutation_enabled / mutation_allowed_services_list 控制
        真实 mutation 执行；默认关闭（返回 disabled），测试可注入覆盖。
        """
        if self._tool_app_service is None:
            from app_v4.tools.application import ToolApplicationService
            s = self.settings
            self._tool_app_service = ToolApplicationService(
                mutation_enabled=s.mutation_enabled,
                allowed_services=s.mutation_allowed_services_list,
            )
        return self._tool_app_service

    @tool_app_service.setter
    def tool_app_service(self, value):
        self._tool_app_service = value

    @property
    def mcp_invoker(self):
        """MCP 工具调用器（§5 Gate 2 #9 / §6 矩阵 #16）。

        默认 LocalToolInvoker（直接调 tool，无需 MCP Server）；
        生产可注入 MCPToolInvoker（走 streamable_http）；
        反作弊测试注入 SpyTransportVerifier 验证调用路径。
        """
        if self._mcp_invoker is None:
            from app_v4.mcp.agent_invoker import LocalToolInvoker
            self._mcp_invoker = LocalToolInvoker()
        return self._mcp_invoker

    @mcp_invoker.setter
    def mcp_invoker(self, value):
        self._mcp_invoker = value

    @property
    def rag_store(self):
        """RAG 检索存储（MilvusRAGStore）。

        懒建：首次访问时通过 store_factory 装配（读取 settings 中的 Milvus / Embedding 配置）。
        装配失败（Milvus 不可达 / Embedding 未配置）时抛出异常，由 rag_search 工具边界
        捕获并转为结构化 unavailable，绝不静默回退。

        测试可注入隔离实例：``deps.rag_store = <fake>``（属性 setter 见下方）。
        """
        if self._rag_store is None:
            from app_v4.rag.store_factory import build_milvus_rag_store
            self._rag_store = build_milvus_rag_store(self.settings)
        return self._rag_store

    @rag_store.setter
    def rag_store(self, value):
        self._rag_store = value

    def get_checkpointer(self):
        """同步 checkpointer（线程安全懒建）。"""
        if self._checkpointer is None:
            from app_v4.memory.checkpointer import build_checkpointer
            self._checkpointer = build_checkpointer(str(self.db_path))
        return self._checkpointer

    async def get_async_checkpointer(self):
        if self._async_checkpointer is None:
            from app_v4.memory.checkpointer import build_async_checkpointer
            self._async_checkpointer = await build_async_checkpointer(str(self.db_path))
        return self._async_checkpointer

    def get_graph(self):
        """同步图（带锁懒建，绑定当前 checkpointer）。"""
        if self._graph is None:
            with self._graph_lock:
                if self._graph is None:
                    from app_v4.graph.builder import _build_graph_with
                    self._graph = _build_graph_with(self.get_checkpointer())
        return self._graph

    async def get_async_graph(self):
        from app_v4.graph.builder import _build_graph_with
        cp = await self.get_async_checkpointer()
        return _build_graph_with(cp)

    async def ainvoke_locked(self, initial, config):
        """在容器级 asyncio.Lock 下执行 ainvoke，序列化对共享 SQLite checkpoint 的写访问。

        多个并发请求共享同一个 AsyncSqliteSaver（同一事件循环内），其写操作
        若同时发生会触发 SQLITE_BUSY。通过锁将各次图执行串行化，避免 SQLite
        写冲突（代价：并发请求的图执行变为串行，但每个请求仍走异步模型）。
        """
        if self._ainvoke_lock is None:
            self._ainvoke_lock = asyncio.Lock()
        async with self._ainvoke_lock:
            graph = await self.get_async_graph()
            return await graph.ainvoke(initial, config)


# ---------------------------------------------------------------------------
# 上下文变量：当前线程/协程使用的容器
# ---------------------------------------------------------------------------
_current_deps: contextvars.ContextVar[Dependencies | None] = contextvars.ContextVar(
    "_current_deps", default=None
)
# 全局默认容器（懒初始化）
_default_deps: Dependencies | None = None
_default_deps_lock = threading.Lock()


def get_deps() -> Dependencies:
    """获取当前活动的容器（上下文覆盖 > 全局默认）。"""
    deps = _current_deps.get()
    if deps is not None:
        return deps
    global _default_deps
    if _default_deps is None:
        with _default_deps_lock:
            if _default_deps is None:
                from app_v4.settings import load_settings
                _default_deps = build_dependencies(load_settings())
    return _default_deps


def set_deps(deps: Dependencies) -> contextvars.Token:
    """激活一个容器（返回 token 供 reset_deps 恢复）。"""
    return _current_deps.set(deps)


def reset_deps(token: contextvars.Token) -> None:
    """恢复到 set_deps 之前的状态。"""
    _current_deps.reset(token)


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------
def build_dependencies(
    settings: Settings,
    *,
    model: Model | None = None,
    cache: ToolCacheProtocol | None = None,
    limiter: LimiterProtocol | None = None,
    clock: Clock | None = None,
) -> Dependencies:
    """根据配置构建一个完整容器。

    MCP invoker 注入规则（§6 矩阵 #16 / HANDOFF 下一步 #3）：
      - settings.mcp_server_url 非空 → 注入 MCPToolInvoker(base_url=...)，
        生产流量经 streamable_http 走原生 MCP Server；
      - 空 + use_fake_model=True（测试/开发）→ 不预置 _mcp_invoker，
        mcp_invoker 属性懒建为 LocalToolInvoker，避免测试访问网络；
      - 空 + use_fake_model=False（真实模型生产）→ fail-fast，
        禁止静默使用 LocalToolInvoker（修复 MCP finding #1）。
    """
    deps = Dependencies(
        settings=settings,
        db_path=settings.resolved_db_path(),
        _model=model,
        _cache=cache,
        _limiter=limiter,
        _clock=clock or _RealClock(),
    )
    if settings.mcp_server_url:
        from app_v4.mcp.agent_invoker import MCPToolInvoker
        deps._mcp_invoker = MCPToolInvoker(base_url=settings.mcp_server_url)
    elif not settings.use_fake_model:
        # 真实模型生产启动不得因 MCP_SERVER_URL 为空而静默使用 LocalToolInvoker
        raise RuntimeError(
            "生产环境（use_fake_model=false）要求 MCP_SERVER_URL 非空。"
            "当前 mcp_server_url 为空，无法建立 MCP 连接。"
            "请设置 MCP_SERVER_URL（如 http://127.0.0.1:8001/mcp）。"
            "LocalToolInvoker 仅允许显式测试/开发注入。"
        )
    return deps
