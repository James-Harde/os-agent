"""FastAPI 入口 — app_v4。

架构：
  - create_app(settings, dependencies) 工厂，避免导入时固化环境变量、
    限流器、数据库和模型单例（符合任务规范 §4.1）。
  - 所有需要外部依赖（审计、审批、限流）的处理器都从 request.app.state.deps
    获取当前容器，保证测试注入的隔离容器在请求处理中可见。

提供的端点：
  - POST /api/chat        同步对话
  - POST /api/chat/stream SSE 流式对话
  - GET  /api/health
  - GET  /api/traces/{run_id}
  - GET  /api/audit
  - GET/POST /api/approvals/{...}

标准 MCP 传输由独立 FastMCP Server 提供（Streamable HTTP，路径 /mcp）。
"""

import contextlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

logger = logging.getLogger("app_v4.mcp")

from app_v4.graph.runner import arun_agent, streaming_agent
from app_v4.settings import Settings, load_settings
from app_v4.container import Dependencies, build_dependencies

# 模块级 limiter（SlowAPI 装饰器需要）；实际限流逻辑走容器令牌桶
_limiter = Limiter(key_func=get_remote_address)


class _ClosingStreamingResponse(StreamingResponse):
    """断连时显式关闭 body iterator，覆盖取消发生在 ASGI send 的窗口。"""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                # 清理失败不得覆盖原始网络断连或应用异常。
                with contextlib.suppress(Exception):
                    await aclose()


# ---------------------------------------------------------------------------
# 异常处理器（通过 register_exception_handlers 挂到具体 app 实例）
# ---------------------------------------------------------------------------
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "请求过于频繁"})


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None
    user_id: str | None = None  # §6 Gate 6：稳定用户标识；匿名（None）不启用跨 thread 记忆


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def create_app(
    settings: Settings | None = None,
    dependencies: Dependencies | None = None,
) -> FastAPI:
    """构建 FastAPI 应用。

    Args:
        settings: 配置；None 则从环境变量/.env 加载。        dependencies: 依赖容器；None 则用 settings 构建默认容器。
    """
    if settings is None:
        settings = load_settings()
    if dependencies is None:
        dependencies = build_dependencies(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """FastAPI 异步生命周期：管理容器资源的创建与销毁。

        启动时不做额外操作（依赖仍按需懒建）；关闭时调用 ``Dependencies.aclose()``
        显式关闭 AsyncSqliteSaver 的 aiosqlite 连接（及其 worker 线程），
        避免事件循环关闭后残留线程触发 ``PytestUnhandledThreadExceptionWarning``。

        真实 Uvicorn 与 TestClient 都经过此生命周期，不再通过替换
        ``app.router.lifespan_context`` 来掩盖资源泄漏。
        """
        try:
            yield
        finally:
            try:
                await dependencies.aclose()
            except Exception as exc:  # pragma: no cover - 防御性兜底
                logger.warning("aclose failed: %s", type(exc).__name__)

    app = FastAPI(
        title=settings.app_name,
        description="可运行、可测试、可面试讲解的 Agent 成品。",
        version=settings.app_version,
        lifespan=lifespan,
    )

    # 把容器挂在 app.state，请求处理器通过 request.app.state.deps 访问
    app.state.deps = dependencies
    app.state.settings = settings

    # 异常处理器
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # 静态前端
    _static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    # 限流中间件
    if settings.rate_limit_enabled:
        app.add_middleware(SlowAPIMiddleware)
        app.state.limiter = _limiter

    _register_routes(app)
    return app


def _get_deps(request: Request) -> Dependencies:
    """从当前请求的 app.state 获取依赖容器。"""
    return request.app.state.deps


def _register_routes(app: FastAPI) -> None:
    """注册所有路由（解耦便于测试复用）。"""

    @app.get("/")
    def index():
        _static_dir = Path(__file__).resolve().parent / "static"
        index_file = _static_dir / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="frontend not found")
        return FileResponse(str(index_file), media_type="text/html; charset=utf-8")

    # ---- 令牌桶限流依赖（基于容器 limiter）----
    async def rate_limit_dependency(request: Request):
        deps = _get_deps(request)
        if not deps.settings.rate_limit_enabled:
            return
        client_ip = get_remote_address(request)
        allowed, headers = deps.limiter.allow(client_ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试",
                headers={**headers, "Retry-After": "60"},
            )

    # ---- 同步对话 ----
    async def _chat_handler(request: Request, body: ChatRequest) -> dict:
        try:
            # 显式传入当前 app 的依赖容器，保证图 / 审计 / 记忆 / 审批 / 模型
            # 全部使用与当前 FastAPI app 绑定的同一个 Dependencies（修复 deps 隔离）。
            # 在 ASGI 事件循环中直接执行异步图，避免多线程 asyncio.run() 各自持有
            # 独立 AsyncSqliteSaver 导致 SQLite 写冲突（并发请求安全）。
            deps = _get_deps(request)
            return await arun_agent(body.message, body.thread_id, body.user_id, deps=deps)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,                detail=f"internal error: {type(exc).__name__}",
            ) from exc

    if app.state.settings.rate_limit_enabled:
        app.post("/api/chat")(_limiter.limit("10/minute")(_chat_handler))
    else:
        app.post("/api/chat")(_chat_handler)

    # ---- 流式对话（Gate 5：token 流 + 客户端取消传播）----
    @app.post("/api/chat/stream")
    async def chat_stream(
        request: Request,
        body: ChatRequest,
        _: None = Depends(rate_limit_dependency),
    ) -> StreamingResponse:
        async def event_generator():
            # streaming_agent 在完整流生命周期内保持请求容器 ContextVar。
            deps = _get_deps(request)
            agen = streaming_agent(body.message, body.thread_id, body.user_id, deps=deps)
            try:
                async for event in agen:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                await agen.aclose()

        # Starlette 监听 http.disconnect 并取消发送 task；自定义 response 再保证
        # 即使取消落在 await send，也会立刻关闭 body iterator。
        return _ClosingStreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- 健康 / Trace / 审计 ----
    @app.get("/api/health")
    def health(request: Request) -> dict:
        deps = _get_deps(request)
        return {
            "status": "ok",
            "version": deps.settings.app_version,
            "engine": "langgraph",
            "kill_switch": deps.settings.kill_switch,
        }

    @app.get("/api/traces/{run_id}")
    def get_trace(run_id: str, request: Request) -> dict:
        deps = _get_deps(request)
        trace = deps.audit_logger.get_trace(run_id)
        if trace is None:
            raise HTTPException(status_code=404, detail=f"trace not found: {run_id}")
        return trace

    @app.get("/api/audit")
    def list_audit(request: Request, limit: int = 30) -> dict:
        deps = _get_deps(request)
        return {"items": deps.audit_logger.list_logs(limit=limit)}

    # ---- 审批 API ----
    @app.get("/api/approvals/{approval_id}")
    def get_approval(approval_id: str, request: Request) -> dict:
        deps = _get_deps(request)
        record = deps.approval_store.get(approval_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"approval not found: {approval_id}")
        return record

    @app.post("/api/approvals/{approval_id}/approve")
    def approve(approval_id: str, request: Request) -> dict:
        deps = _get_deps(request)
        record = deps.approval_store.approve(approval_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"approval not found: {approval_id}")
        return {"status": "ok", "approval": record}

    @app.post("/api/approvals/{approval_id}/reject")
    def reject(approval_id: str, request: Request) -> dict:
        deps = _get_deps(request)
        record = deps.approval_store.reject(approval_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"approval not found: {approval_id}")
        return {"status": "ok", "approval": record}

    @app.get("/api/approvals")
    def list_approvals(
        request: Request, status_filter: str | None = None, limit: int = 30,
    ) -> dict:
        deps = _get_deps(request)
        rows = deps.approval_store.list(status_filter=status_filter, limit=limit)
        return {"items": rows}

    # ---- HITL 审批恢复 ----
    @app.post("/api/approvals/{approval_id}/resume")
    async def resume_after_approval(approval_id: str, request: Request) -> dict:
        from app_v4.approval.interrupt import resume_command

        deps = _get_deps(request)
        record = deps.approval_store.get(approval_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"approval not found: {approval_id}")
        if record["status"] not in ("approved", "rejected"):
            raise HTTPException(
                status_code=400,
                detail="审批单仍在 pending 状态，请先调用 approve 或 reject",
            )

        config = {"configurable": {"thread_id": record["thread_id"]}}
        decision = record["status"]
        run_id = record["run_id"]
        thread_id = record["thread_id"]
        try:
            # resume 期间 set contextvar，使节点内 get_deps() 解析到当前 app 容器
            from app_v4.container import set_deps, reset_deps
            tok = set_deps(deps)
            try:
                final_state = await deps.ainvoke_locked(
                    resume_command(decision),
                    config,
                )
            finally:
                reset_deps(tok)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"恢复执行失败: {type(exc).__name__}",
            ) from exc

        # 写最终 Trace：resume 后按原 run_id 覆盖审计记录，
        # 使 /api/traces/{run_id} 可查询到最终状态（不再永远停在 pending）。
        answer = final_state.get("answer", "")
        tool_calls = final_state.get("tool_calls", [])
        trace_steps = final_state.get("trace_steps", [])
        result = {
            "run_id": run_id,
            "thread_id": thread_id,
            "intent": final_state.get("intent", ""),
            "guard_decision": final_state.get("guard_decision", "allow"),
            "guard_reasons": final_state.get("guard_reasons", []),
            "tool_calls": tool_calls,
            "answer": answer,
            "answer_source": final_state.get("answer_source", ""),
            "trace_steps": trace_steps,
        }
        deps.audit_logger.record(thread_id, result)

        return {
            "status": "ok",
            "decision": decision,
            "approval_id": approval_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "answer": answer,
            "tool_calls": tool_calls,
            "trace_steps": trace_steps,
        }

    # 标准 MCP 传输由独立 FastMCP Server 提供（Streamable HTTP，路径 /mcp）。
    # 不再提供 /api/mcp JSON-RPC 兼容端点——禁止手写 JSON-RPC 与官方 SDK 并存。


# ---------------------------------------------------------------------------
# 默认应用实例（向后兼容：直接 `from app_v4.main import app` 仍可用）
# ---------------------------------------------------------------------------
app = create_app()
