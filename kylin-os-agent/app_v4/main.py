"""FastAPI 入口 — app_v4。

对比 app_v2 新增：
  - GET  /api/traces/{run_id} — 按 run_id 查询单次 Run 的完整 Trace
  - GET  /api/audit        — 查询审计日志列表
  - /api/chat 返回结构新增 run_id / thread_id / trace_summary
  - thread_id 不再默认 "default"
"""

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

logger = logging.getLogger("app_v4.mcp")

from app_v4.graph.runner import run_agent, streaming_agent
from app_v4.audit.logger import get_audit_logger
from app_v4.approval.store import get_approval_store
from app_v4.mcp.server import MCPServer

_mcp_server = MCPServer()


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Kylin Secure OS Agent v4",
    description="可运行、可测试、可面试讲解的 Agent 成品。",
    version="0.4.0",
)
# 测试时可通过环境变量关闭限流
import os
_RATE_LIMIT_ENABLED = os.getenv("APP_V4_DISABLE_RATE_LIMIT", "").lower() not in ("1", "true", "yes")
if _RATE_LIMIT_ENABLED:
    # 必须同时挂载 SlowAPIMiddleware，否则 @limiter.limit 不会生效
    app.add_middleware(SlowAPIMiddleware)
    app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "请求过于频繁"})


# ---------------------------------------------------------------------------
# P4: 静态前端托管（单页 HTML，无构建流程）
# ---------------------------------------------------------------------------
_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
def index():
    """首页：返回前端单页。

    使用 FileResponse 而非 StreamingResponse(open(...))，
    避免文件描述符泄漏（StreamingResponse 不会关闭同步迭代器）。
    """
    index_file = _static_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="frontend not found")
    return FileResponse(str(index_file), media_type="text/html; charset=utf-8")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None


def _chat_handler(request: Request, body: ChatRequest) -> dict:
    """/api/chat 的实际处理逻辑。"""
    try:
        return run_agent(body.message, body.thread_id)
    except HTTPException:
        raise
    except Exception as exc:
        # 不泄露密钥或完整堆栈
        raise HTTPException(status_code=500, detail=f"internal error: {type(exc).__name__}") from exc


# 根据是否启用限流，注册不同版本的路由：
#  - 启用时：用 @limiter.limit 装饰，限制 10次/分钟
#  - 关闭时：直接注册裸处理器（测试环境）
if _RATE_LIMIT_ENABLED:
    app.post("/api/chat")(limiter.limit("10/minute")(_chat_handler))
else:
    app.post("/api/chat")(_chat_handler)


@app.post("/api/chat/stream")
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """SSE 流式对话端点。

    渐进式返回：
      preflight → plan（含渐进披露信息）→ execute（工具调用结果）→ summarize（最终 answer）→ done

    每条事件为 "data: {json}\\n\\n" 标准 SSE 格式。
    """
    async def event_generator():
        async for event in streaming_agent(body.message, body.thread_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": "0.4.0", "engine": "langgraph"}


@app.get("/api/traces/{run_id}")
def get_trace(run_id: str) -> dict:
    """按 run_id 查询单次 Run 的完整记录（含 tool_calls + trace_steps）。"""
    logger = get_audit_logger()
    trace = logger.get_trace(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"trace not found: {run_id}")
    return trace


@app.get("/api/audit")
def list_audit(limit: int = 30) -> dict:
    """查询审计日志列表。"""
    logger = get_audit_logger()
    return {"items": logger.list_logs(limit=limit)}


# ---------------------------------------------------------------------------
# P1: 审批 API
# ---------------------------------------------------------------------------
@app.get("/api/approvals/{approval_id}")
def get_approval(approval_id: str) -> dict:
    """查询单个审批单状态。"""
    store = get_approval_store()
    record = store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"approval not found: {approval_id}")
    return record


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str) -> dict:
    """批准（幂等：重复调用不产生副作用）。"""
    store = get_approval_store()
    record = store.approve(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"approval not found: {approval_id}")
    return {"status": "ok", "approval": record}


@app.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: str) -> dict:
    """拒绝（幂等）。"""
    store = get_approval_store()
    record = store.reject(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"approval not found: {approval_id}")
    return {"status": "ok", "approval": record}


# ---------------------------------------------------------------------------
# P2: MCP JSON-RPC 端点
# ---------------------------------------------------------------------------
@app.post("/api/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """MCP JSON-RPC HTTP 入口。（生产环境应使用 SSE/stdio 传输）"""
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        # 请求体不是合法 JSON：返回 JSON-RPC 标准解析错误，而非静默吞掉
        logger.warning("MCP 请求 JSON 解析失败: %s", exc)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error: request body is not valid JSON"}},
            status_code=400,
        )
    except Exception as exc:
        # 其他读取异常（空体、编码错等）：同样返回解析错误，并记录错误类型
        logger.warning("MCP 请求体读取异常 [%s]: %s", type(exc).__name__, exc)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error: cannot read request body"}},
            status_code=400,
        )
    result = _mcp_server.handle(body)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# P2: 标准 MCP Streamable HTTP 传输（官方 SDK）
#
# 修复 audit #11：除自制 JSON-RPC 分发器外，额外暴露标准 MCP 传输端点，
# 支持官方 MCP Client 通过 streamable_http 完成 initialize/list/call。
# ---------------------------------------------------------------------------
try:
    from app_v4.mcp.native_server import mcp as _fastmcp_server
    # streamable_http_app 内部路由是 /mcp，挂载到 /mcp 后完整路径是 /mcp/mcp
    # 为避免双重路径，直接挂载到根路径的 /mcp 子路径
    app.mount("/mcp", _fastmcp_server.streamable_http_app(), name="mcp-standard")
    logger.info("标准 MCP Streamable HTTP 已挂载到 /mcp（完整路径 /mcp/mcp）")
except Exception as exc:
    logger.warning("标准 MCP 传输未启用: %s", exc)


@app.get("/api/approvals")
def list_approvals(status_filter: str | None = None, limit: int = 30) -> dict:
    """查询审批单列表。"""
    from app_v4.approval.store import get_approval_store

    # 复用审批单存储的单例连接，避免另开独立连接与 checkpointer 竞争写锁
    store = get_approval_store()
    rows = store.list(status_filter=status_filter, limit=limit)
    return {"items": rows}
