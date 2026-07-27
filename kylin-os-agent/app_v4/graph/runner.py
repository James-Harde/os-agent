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
import time
import uuid
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphInterrupt

from app_v4.graph.builder import get_graph, get_async_graph
from app_v4.audit.logger import get_audit_logger
from app_v4.memory.long_term import get_long_term_memory
from app_v4.container import get_deps, set_deps, reset_deps
from app_v4.settings import Settings


def run_agent(
    user_input: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
    *,
    deps: "Dependencies | None" = None,
) -> dict[str, Any]:
    """运行一次 agent 对话（sync — 使用 SyncSqliteSaver，无事件循环绑定问题）。

    Gate 5：token 流经 fake model 的 _astream 产出，写入 state["stream_tokens"]。
    Gate 6：user_id 用于长期记忆隔离（匿名用户不启用跨 thread 记忆）。

    deps：显式传入当前 app 的依赖容器。提供后，图 / 审计 / 记忆 / 审批 / 模型
    全部走该容器（含节点内部通过 get_deps() 解析的上下文），保证与当前 FastAPI app
    一致；后台线程场景下会在调用前 set contextvar 以传播依赖上下文。
    """
    # 解析有效容器：显式传入 > 当前上下文 > 全局默认
    eff_deps = deps if deps is not None else get_deps()
    # 设置 contextvar，使节点内部 get_chat_model / get_approval_store / get_long_term_memory
    # 等调用解析到同一容器（修复 deps 隔离：不依赖测试进程全局环境碰巧一致）
    token = set_deps(eff_deps)
    try:
        return _run_agent_impl(user_input, conversation_id, user_id, eff_deps)
    finally:
        reset_deps(token)


def _run_agent_impl(
    user_input: str,
    conversation_id: str | None,
    user_id: str | None,
    eff_deps,
) -> dict[str, Any]:
    """run_agent 的实际实现（已持有 contextvar + 有效 deps）。"""
    # §5 Gate 5 #9：kill switch 在 run 入口生效（早于规划/执行）。
    from app_v4.graph.budget import BudgetConfig
    if BudgetConfig.check_kill_switch():
        run_id = str(uuid.uuid4())
        thread_id = conversation_id or str(uuid.uuid4())
        return {
            "run_id": run_id,
            "thread_id": thread_id,
            "intent": "",
            "guard_decision": "deny",
            "guard_reasons": ["系统熔断开关已激活（APP_V4_KILL_SWITCH=true），所有操作暂停"],
            "tool_calls": [],
            "answer": "系统处于熔断状态，所有操作已暂停。请稍后再试或联系管理员关闭熔断开关。",
            "answer_source": "kill_switch",
            "trace_summary": {"total_steps": 0, "total_duration_ms": 0, "steps": []},
            "trace_steps": [],
            "status": "cancelled",
        }

    # 自动生成唯一 thread_id（修复 B06）
    thread_id = conversation_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial = {
        "messages": [HumanMessage(content=user_input)],
        "run_id": run_id,
        "thread_id": thread_id,
        "user_id": user_id or "",  # Gate 6：'' 表示匿名，不启用跨 thread 记忆
        # 每次 run 必须显式重置的字段（否则上轮值通过 checkpoint 污染下轮）
        "intent": "",
        "plan": [],
        "tool_calls": [],
        "guard_decision": "allow",
        "guard_reasons": [],
        "trace_steps": [],
        "pending_approvals": [],
        # Phase F：预算/熔断字段（每次 run 从零开始）
        "step_count": 0,
        "tool_call_count": 0,
        "budget_exceeded": False,
        # §4.2 #3：循环检测仅限同一 run 内部迭代。每次 run 必须重置，
        # 否则通过 checkpoint 跨 turn 合法重复问题被误判为循环。
        "seen_plans": [],
        "loop_detected": False,
        "memory_context": {},
        "executed_approvals": [],
        "stream_tokens": [],
        "cancelled": False,
        # 混合 Agent 主链：场景路由 + 只读 bounded ReAct
        "route": "",
        "readonly_iterations": 0,
        "readonly_tool_calls": 0,
        "readonly_start_time": 0.0,
        "readonly_last_action_key": "",
        "readonly_last_observation_key": "",
        "readonly_no_progress_streak": 0,
        "readonly_error_streak": 0,
        "stop_reason": "",
        "current_action": {},
        "readonly_exec_result": {},
        "readonly_trace": [],
        "readonly_observations": [],
    }

    graph = eff_deps.get_graph()
    final_state = graph.invoke(initial, config)

    # §5 Gate 2：检测 LangGraph interrupt() 中断（HITL 审批流）。
    # 当 execute_node 调用 interrupt() 时，invoke 返回的 state 含 __interrupt__ 字段。
    interrupts = final_state.get("__interrupt__", [])
    if interrupts:
        # 图被中断在审批点：返回 pending 状态，不伪装完成。
        pending = final_state.get("pending_approvals", [])
        # 写入审计（pending 状态，修复 audit #6：Trace 可查询）
        logger = eff_deps.audit_logger
        logger.record(thread_id, {
            "run_id": run_id,
            "thread_id": thread_id,
            "intent": final_state.get("intent", ""),
            "guard_decision": "allow",
            "guard_reasons": [],
            "tool_calls": final_state.get("tool_calls", []),
            "answer": "",
            "answer_source": "pending_approval",
            "trace_steps": final_state.get("trace_steps", []),
        })
        return {
            "run_id": run_id,
            "thread_id": thread_id,
            "intent": final_state.get("intent", ""),
            "guard_decision": "allow",
            "guard_reasons": [],
            "tool_calls": final_state.get("tool_calls", []),
            "answer": "",
            "answer_source": "pending_approval",
            "status": "pending_approval",
            "approval_required": True,
            "pending_approvals": pending,
            "route": final_state.get("route", ""),
            "trace_summary": {
                "total_steps": len(final_state.get("trace_steps", [])),
                "total_duration_ms": round(
                    sum(s.get("duration_ms", 0) for s in final_state.get("trace_steps", [])), 2
                ),
                "steps": [s["node"] for s in final_state.get("trace_steps", [])],
            },
            "trace_steps": final_state.get("trace_steps", []),
        }

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
        "user_id": final_state.get("user_id", ""),  # Gate 6：记忆隔离
        # 混合 Agent 主链：场景路由 + 只读 ReAct
        "route": final_state.get("route", ""),
        "stop_reason": final_state.get("stop_reason", ""),
        "readonly_iterations": final_state.get("readonly_iterations", 0),
        "readonly_trace": final_state.get("readonly_trace", []),
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
    logger = eff_deps.audit_logger
    logger.record(thread_id, result)

    # P3: 写入长期记忆（结论 + 画像累积），含 user_id 以支持跨 thread 召回
    memory = eff_deps.long_term_memory
    memory.record(
        thread_id=thread_id,
        run_id=run_id,
        intent=result["intent"],
        answer=result["answer"],
        answer_source=result["answer_source"],
        user_id=result.get("user_id", ""),
    )

    return result


async def streaming_agent(
    user_input: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
    *,
    deps: "Dependencies | None" = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """流式运行 agent — 产出节点事件 + 模型 token 事件，供 SSE 端点使用。

    Gate 5：token 事件来自模型 astream（通过 LangGraph astream_events 捕获
    on_chat_model_stream），不是把完整答案手工切字符串。
    Gate 6：user_id 用于记忆隔离。

    事件类型（event 字段）：
      - "preflight"  : 预检完成
      - "plan"      : 规划完成
      - "token"     : 模型产出单个 token（含 index/delta）
      - "execute"   : 工具执行完成
      - "summarize" : 总结开始（流式）
      - "loop"      : 循环熔断
      - "deny"      : 拒绝回答
      - "done"      : 流结束（含 TTFT、总耗时、token 数）
    """
    import threading

    # 解析有效容器（同 run_agent）：显式传入 > 当前上下文 > 全局默认
    eff_deps = deps if deps is not None else get_deps()

    thread_id = conversation_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Gate 5 #4：注册 shared state，使客户端取消（cancel_run）可触达。
    # cancelled 标志同时被 streaming_agent 轮询循环检测。
    register_streaming_run(thread_id, {})  # 占位，稍后更新为 shared

    initial = {
        "messages": [HumanMessage(content=user_input)],
        "run_id": run_id,
        "thread_id": thread_id,
        "user_id": user_id or "",  # Gate 6：user_id 记忆隔离
        "trace_steps": [],
        "pending_approvals": [],
        "step_count": 0,
        "tool_call_count": 0,
        "budget_exceeded": False,
        "loop_detected": False,
        "memory_context": {},
        "seen_plans": [],
        "intent": "",
        "plan": [],
        "tool_calls": [],
        "guard_decision": "allow",
        "guard_reasons": [],
        "answer": "",
        "answer_source": "",
        "executed_approvals": [],
        "stream_tokens": [],
        "cancelled": False,
        # 混合 Agent 主链：场景路由 + 只读 bounded ReAct
        "route": "",
        "readonly_iterations": 0,
        "readonly_tool_calls": 0,
        "readonly_start_time": 0.0,
        "readonly_last_action_key": "",
        "readonly_last_observation_key": "",
        "readonly_no_progress_streak": 0,
        "readonly_error_streak": 0,
        "stop_reason": "",
        "current_action": {},
        "readonly_exec_result": {},
        "readonly_trace": [],
        "readonly_observations": [],
    }

    # Gate 5：shared state，模型 astream 实时写入 stream_tokens。
    shared: dict[str, Any] = dict(initial)
    # 用真实的 shared 替换占位注册（使 cancel_run 可触达此 dict）
    register_streaming_run(thread_id, shared)
    t_start = time.monotonic()
    ttft: float | None = None
    token_count = 0
    graph_err: list[BaseException] = []
    done_event = threading.Event()

    def _run_graph():
        # 后台线程不继承父线程 contextvar，必须显式 set 以传播依赖上下文。
        # 这样节点内 get_chat_model / get_approval_store / get_long_term_memory
        # 解析到与当前 app 同一个容器（修复 deps 隔离）。
        tok = set_deps(eff_deps)
        try:
            g = eff_deps.get_graph()  # sync graph + SyncSqliteSaver（无事件循环绑定问题）
            try:
                g.invoke(shared, config)
            except GraphInterrupt:
                # 旧版 LangGraph 以异常形式中断；新版存于 get_state().tasks。
                pass
            # 从 checkpoint 读最终状态（sync invoke 不把 return 合并回 shared）
            final = g.get_state(config)
            shared.update(final.values)
            # 检测 HITL interrupt：任务含 interrupts 说明图停在审批点。
            tasks = getattr(final, "tasks", ())
            interrupted = any(getattr(t, "interrupts", None) for t in tasks)
            shared["__interrupted__"] = interrupted
        except BaseException as exc:
            graph_err.append(exc)
        finally:
            reset_deps(tok)
            done_event.set()

    # Gate 5 #4：后台线程运行图，主协程轮询 token 并监听取消。
    runner_th = threading.Thread(target=_run_graph, daemon=True)
    runner_th.start()

    poll_interval = 0.02  # 20ms
    while not done_event.is_set():
        if shared.get("cancelled"):
            break
        tokens = shared.get("stream_tokens", [])
        while token_count < len(tokens):
            text = tokens[token_count]
            token_count += 1
            if ttft is None:
                ttft = round((time.monotonic() - t_start) * 1000, 2)
            yield {
                "event": "token",
                "run_id": run_id,
                "thread_id": thread_id,
                "delta": text,
                "index": token_count - 1,
            }
        time.sleep(poll_interval)

    # 排空剩余 token
    tokens = shared.get("stream_tokens", [])
    while token_count < len(tokens):
        text = tokens[token_count]
        token_count += 1
        if ttft is None:
            ttft = round((time.monotonic() - t_start) * 1000, 2)
        yield {
            "event": "token",
            "run_id": run_id,
            "thread_id": thread_id,
            "delta": text,
            "index": token_count - 1,
        }

    cancelled = shared.get("cancelled")

    # 节点事件：从 trace_steps 重建（保持旧接口：preflight/plan/execute/summarize/deny）
    node_event_emitted = set()
    for step in shared.get("trace_steps", []):
        node_name = step.get("node", "")
        if node_name in ("preflight", "plan", "execute", "summarize", "deny") and node_name not in node_event_emitted:
            node_event_emitted.add(node_name)
            ev = _node_to_event(node_name, shared, run_id, thread_id)
            if ev is not None:
                yield ev

    # 写入审计 + 记忆
    result = {
        "run_id": run_id,
        "thread_id": thread_id,
        "intent": shared.get("intent", ""),
        "guard_decision": shared.get("guard_decision", "allow"),
        "guard_reasons": shared.get("guard_reasons", []),
        "tool_calls": shared.get("tool_calls", []),
        "answer": shared.get("answer", ""),
        "answer_source": shared.get("answer_source", ""),
        "trace_steps": shared.get("trace_steps", []),
    }
    logger = eff_deps.audit_logger
    logger.record(thread_id, result)
    memory = eff_deps.long_term_memory
    memory.record(
        thread_id=thread_id,
        run_id=run_id,
        intent=result["intent"],
        answer=result["answer"],
        answer_source=result["answer_source"],
        user_id=shared.get("user_id", ""),
    )

    total_ms = round((time.monotonic() - t_start) * 1000, 2)

    if cancelled:
        yield {
            "event": "cancelled",
            "run_id": run_id,
            "thread_id": thread_id,
            "message": "客户端取消",
            "stream_stats": {"ttft_ms": ttft, "total_ms": total_ms, "token_count": token_count},
        }
        unregister_streaming_run(thread_id)
        return

    if graph_err:
        unregister_streaming_run(thread_id)
        raise graph_err[0]

    # HITL interrupt：图停在审批点，发 approval_required 事件（含审批卡所需字段），
    # 不发"空答案但看似完成"的 done（修复 B5）。
    if shared.get("__interrupted__"):
        pending = shared.get("pending_approvals", [])
        for p in pending:
            yield {
                "event": "approval_required",
                "run_id": run_id,
                "thread_id": thread_id,
                "approval_id": p.get("approval_id", ""),
                "tool_name": p.get("tool_name", ""),
                "arguments": p.get("arguments", {}),
                "reason": p.get("reason", ""),
                "risk_level": p.get("risk_level", "medium"),
                "status": "pending",
            }
        unregister_streaming_run(thread_id)
        return

    yield {
        "event": "done",
        "run_id": run_id,
        "thread_id": thread_id,
        "intent": result["intent"],
        "guard_decision": result["guard_decision"],
        "answer": result["answer"],
        "answer_source": result["answer_source"],
        "stream_stats": {
            "ttft_ms": ttft,
            "total_ms": total_ms,
            "token_count": token_count,
            "chunk_count": token_count,
        },
    }
    unregister_streaming_run(thread_id)


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


# ---------------------------------------------------------------------------
# 客户端取消（Gate 5 #4）：注册表 + 取消函数
# ---------------------------------------------------------------------------
_streaming_run_state: dict[str, dict[str, Any]] = {}


def register_streaming_run(thread_id: str, state: dict[str, Any]) -> None:
    """注册一个正在流式运行的 shared state（供后续取消）。"""
    _streaming_run_state[thread_id] = state


def cancel_run(thread_id: str) -> bool:
    """标记某 thread 的流式运行为已取消。

    Gate 5 #4：传播 cancellation。streaming_agent 轮询时检测到 cancelled=True
    即停止排出 token 并发出 cancelled 事件。
    """
    state = _streaming_run_state.get(thread_id)
    if state is not None:
        state["cancelled"] = True
        return True
    return False


def unregister_streaming_run(thread_id: str) -> None:
    """流式运行结束后清理注册。"""
    _streaming_run_state.pop(thread_id, None)


def cleanup_all_streaming_runs() -> None:
    """测试用：清理所有注册。"""
    _streaming_run_state.clear()
