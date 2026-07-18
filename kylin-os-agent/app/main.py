from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.agent.orchestrator import AgentOrchestrator
from app.approval.service import ApprovalService
from app.audit.logger import AuditLogger
from app.config import DATA_DIR, STATIC_DIR
from app.mcp.schemas import JSONRPCRequest
from app.mcp.server import MCPServer
from app.memory.store import MemoryStore
from app.model.adapter import LLMConfigurationError, ModelAdapter
from app.safety.guard import SafetyGuard
from app.tools.registry import ToolRegistry


DATA_DIR.mkdir(parents=True, exist_ok=True)

audit_logger = AuditLogger()
memory_store = MemoryStore()
tool_registry = ToolRegistry(audit_logger=audit_logger)
safety_guard = SafetyGuard()
model_adapter = ModelAdapter()
approval_service = ApprovalService()
orchestrator = AgentOrchestrator(
    tool_registry=tool_registry,
    safety_guard=safety_guard,
    audit_logger=audit_logger,
    memory_store=memory_store,
    model_adapter=model_adapter,
    approval_service=approval_service,
)

limiter = Limiter(key_func=get_remote_address)

mcp_server = MCPServer(
    tool_registry=tool_registry,
    safety_guard=safety_guard,
    audit_logger=audit_logger,
)

app = FastAPI(
    title="Kylin Secure OS Agent",
    description="A safety-first intelligent operations agent skeleton for Kylin/Linux OS.",
    version="0.1.0",
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后再试"},
    )


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "kylin-secure-os-agent",
        "version": "0.1.0",
    }


@app.get("/api/runtime")
def runtime() -> dict:
    status = model_adapter.status()
    status["sandbox"] = tool_registry.sandbox_status()
    return status


@app.get("/api/tools")
def list_tools() -> dict:
    return {"tools": tool_registry.list_specs()}


@app.get("/api/audit")
def list_audit(limit: int = 30) -> dict:
    return {"items": audit_logger.list_audit_logs(limit=limit)}


@app.get("/api/approvals")
def list_approvals(limit: int = 30) -> dict:
    return {"items": approval_service.list_all(limit=limit)}


class ApprovalDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=80)
    reason: str = ""


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, body: ApprovalDecision) -> dict:
    row = approval_service.decide(
        approval_id=approval_id,
        decided_by=body.decided_by,
        approve=True,
        reason=body.reason,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="审批单不存在或已处理")
    return {"status": "ok", "approval": row}


@app.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: str, body: ApprovalDecision) -> dict:
    row = approval_service.decide(
        approval_id=approval_id,
        decided_by=body.decided_by,
        approve=False,
        reason=body.reason,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="审批单不存在或已处理")
    return {"status": "ok", "approval": row}


@app.get("/api/conversations")
def list_conversations(limit: int = 20) -> dict:
    return {"items": memory_store.list_conversations(limit=limit)}


@app.post("/api/mcp")
def mcp_endpoint(body: JSONRPCRequest) -> dict:
    result = mcp_server.handle_request(body)
    return {"jsonrpc": "2.0", "id": body.id, "result": result}


@app.get("/api/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str, limit: int = 20) -> dict:
    return {"items": memory_store.recent_messages(conversation_id=conversation_id, limit=limit)}


@app.post("/api/chat")
@limiter.limit("10/minute")
def chat(request: Request, body: ChatRequest) -> dict:
    try:
        return orchestrator.handle(body.message, conversation_id=body.conversation_id)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
