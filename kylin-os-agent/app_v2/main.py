"""FastAPI 入口 — LangGraph 版。

教学要点：
  对比旧版 main.py，唯一的变化是把 orchestrator 换成了 runner。
  API 端点、限流、请求/响应格式 —— 这些是"业务层"，框架无关，不改动。

  这说明一个重要事实：LangChain/LangGraph 替代的是 agent 内部的编排，
  API 层、前端、MCP 协议层这些都不受影响。
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app_v2.graph.runner import run_agent


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Kylin Secure OS Agent v2 (LangGraph)",
    description="LangGraph 重构版 agent。",
    version="0.2.0",
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "请求过于频繁"})


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None

@app.get("/api/zby")
print("zby")

@app.post("/api/chat")
@limiter.limit("10/minute")
def chat(request: Request, body: ChatRequest) -> dict:
    try:
        return run_agent(body.message, body.conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": "0.2.0", "engine": "langgraph"}
