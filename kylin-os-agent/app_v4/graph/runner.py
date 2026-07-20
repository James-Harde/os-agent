"""图运行的封装 — FastAPI 和图之间的翻译层。

对比 app_v2 改动：
  - 自动生成 UUID 作为 thread_id（修复 B06：不再共用 "default"）
  - 自动生成 run_id 用于 Trace 查询
  - 执行后写入审计日志（修复 B08）
  - 返回 trace_summary
  - P3：运行后写入长期记忆；暴露 streaming_agent 供 SSE 端点使用
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage

from app_v4.graph.builder import get_graph, get_async_graph
from app_v4.audit.logger import get_audit_logger
from app_v4.memory.long_term import get_long_term_memory


def run_agent(
    user_input: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """运行一次 agent 对话。"""
    graph = get_graph()

    # 自动生成唯一 thread_id（修复 B06）
    thread_id = conversation_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    config = {"configurable": {"thread_id": thread_id}}
    initial = {
        "messages": [HumanMessage(content=user_input)],
        "run_id": run_id,
        "thread_id": thread_id,
        # 每次 run 必须显式重置的字段（否则上轮值通过 checkpoint 污染下轮）
        "intent": "",
        "plan": [],
        "tool_calls": [],
        "guard_decision": "allow",
        "guard_reasons": [],
        "trace_steps": [],
        "pending_approvals": [],
        # 注意：seen_plans 故意不放这里 — 它必须由 checkpointer 跨 turn 持久化，
        # 每次 invoke 时 LangGraph 会把 checkpoint 里的值合并进来。
        "loop_detected": False,
        "memory_context": {},
    }

    final_state = graph.invoke(initial, config)

    # 构建返回
    result = {
        "run_id": run_id,
        "thread_id": thread_id,
        "intent": final_state.get("intent", ""),
        "guard_decision": final_state.get("guard_decision", "allow"),
        "guard_reasons": final_state.get("guard_reasons", []),
        "tool_calls": final_state.get("tool_calls", []),
        "answer": final_state.get("answer", ""),
        "answer_source": final_state.get("answer_source", ""),
        "trace_summary": {
            "total_steps": len(final_state.get("trace_steps", [])),
            "total_duration_ms": round(
                sum(s.get("duration_ms", 0) for s in final_state.get("trace_steps", [])), 2
            ),
            "steps": [s["node"] for s in final_state.get("trace_steps", [])],
        },
        # 完整 trace_steps 传给审计日志，供 /api/traces/{run_id} 查询（修复数据丢失）
        "trace_steps": final_state.get("trace_steps", []),
    }

    # P1: 如有待审批项，加到返回里（让调用方知道需要人工审批）
    pending = final_state.get("pending_approvals", [])
    if pending:
        result["approval_required"] = True
        result["pending_approvals"] = pending

    # 写入审计（修复 B08：审计接入主链路）
    logger = get_audit_logger()
    logger.record(thread_id, result)

    # P3: 写入长期记忆（结论 + 画像累积）
    memory = get_long_term_memory()
    memory.record(
        thread_id=thread_id,
        run_id=run_id,
        intent=result["intent"],
        answer=result["answer"],
        answer_source=result["answer_source"],
    )

    return result


async def streaming_agent(
    user_input: str,
    conversation_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """流式运行 agent — 按节点产出增量事件，供 SSE 端点使用。

    事件类型（event 字段）：
      - "preflight"  : 预检完成
      - "plan"      : 规划完成（含意图、工具列表、渐进披露信息）
      - "execute"   : 工具执行完成（含 tool_calls）
      - "summarize" : 总结完成（含 answer）
      - "loop"      : 循环熔断触发
      - "deny"      : 拒绝回答
      - "done"      : 流结束
    """
    graph = await get_async_graph()
    thread_id = conversation_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    initial = {
        "messages": [HumanMessage(content=user_input)],
        "run_id": run_id,
        "thread_id": thread_id,
        "trace_steps": [],
        "pending_approvals": [],
        "loop_detected": False,
        "memory_context": {},
    }

    # astream(stream_mode="updates") 每个 chunk 是一个 {node_name: partial_state} dict
    # 边流边合并成 merged，最终用于构造 done 事件，避免再读一次 DB。
    merged: dict[str, Any] = {}
    async for chunk in graph.astream(initial, config, stream_mode="updates"):
        for node_name, partial in chunk.items():
            merged.update(partial)
            event = _node_to_event(node_name, partial, run_id, thread_id)
            if event is not None:
                yield event

    # 流结束后写入审计 + 记忆（修复 audit #6：流式路径也要写审计/Trace）
    result = {
        "run_id": run_id,
        "thread_id": thread_id,
        "intent": merged.get("intent", ""),
        "guard_decision": merged.get("guard_decision", "allow"),
        "guard_reasons": merged.get("guard_reasons", []),
        "tool_calls": merged.get("tool_calls", []),
        "answer": merged.get("answer", ""),
        "answer_source": merged.get("answer_source", ""),
        "trace_steps": merged.get("trace_steps", []),
    }
    logger = get_audit_logger()
    logger.record(thread_id, result)
    memory = get_long_term_memory()
    memory.record(
        thread_id=thread_id,
        run_id=run_id,
        intent=result["intent"],
        answer=result["answer"],
        answer_source=result["answer_source"],
    )

    # 流结束后发 done 事件，携带最终 answer（从已合并状态取）
    yield {
        "event": "done",
        "run_id": run_id,
        "thread_id": thread_id,
        "intent": result["intent"],
        "guard_decision": result["guard_decision"],
        "answer": result["answer"],
        "answer_source": result["answer_source"],
    }


def _node_to_event(node_name: str, partial: dict[str, Any], run_id: str, thread_id: str) -> dict[str, Any] | None:
    """把节点增量状态翻译为 SSE 事件。"""
    base = {"run_id": run_id, "thread_id": thread_id}
    if node_name == "preflight":
        return {"event": "preflight", **base,
                "risk_level": "high" if partial.get("guard_decision") == "deny" else "low",
                "guard_decision": partial.get("guard_decision", "allow")}
    if node_name == "plan":
        if partial.get("loop_detected"):
            return {"event": "loop", **base, "reason": "循环熔断：检测到重复规划，已停止"}
        return {"event": "plan", **base,
                "intent": partial.get("intent", ""),
                "plan": partial.get("plan", []),
                "progressive": partial.get("memory_context", {}).get("progressive", False),
                "hidden_tools": partial.get("memory_context", {}).get("hidden_tools", [])}
    if node_name == "execute":
        return {"event": "execute", **base, "tool_calls": partial.get("tool_calls", [])}
    if node_name == "summarize":
        return {"event": "summarize", **base,
                "answer": partial.get("answer", ""),
                "answer_source": partial.get("answer_source", "")}
    if node_name == "deny":
        return {"event": "deny", **base,
                "answer": partial.get("answer", ""),
                "answer_source": partial.get("answer_source", "")}
    return None
