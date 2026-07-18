"""FastAPI 入口 —— AgentExecutor 版。

教学要点：
  和 v2 main.py 唯一的区别：
    v2: from app_v2.graph.runner import run_agent
    v3: from app_v3.agent_executor import run_agent

  API 层不变。说明编排层的替换对业务层透明。
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app_v3.agent_executor import run_agent

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="Kylin Secure OS Agent v3 (LangChain AgentExecutor)",
    description="纯 LangChain 版 agent。AgentExecutor 接管循环。",
    version="0.3.0",
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "请求过于频繁"})


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


@app.post("/api/chat")
@limiter.limit("10/minute")
def chat(request: Request, body: ChatRequest) -> dict:
    try:
        return run_agent(body.message, body.conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": "0.3.0", "engine": "langchain-agent-executor"}
