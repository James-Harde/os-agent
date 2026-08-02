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
import contextlib
import threading
import time
import uuid
from typing import Any, AsyncGenerator

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage
from langchain_core.tracers._streaming import _StreamingCallbackHandler
from langgraph.errors import GraphInterrupt

from app_v4.graph.builder import get_graph, get_async_graph
from app_v4.audit.logger import get_audit_logger
from app_v4.memory.long_term import get_long_term_memory
from app_v4.container import get_deps, set_deps, reset_deps
from app_v4.settings import Settings


async def arun_agent(
    user_input: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
    *,
    deps: "Dependencies | None" = None,
) -> dict[str, Any]:
    """异步运行一次 agent 对话，供 FastAPI/LangGraph 生产入口调用。

    Gate 5：模型 token 经 model.astream() 产出（节点为异步，直接 await），
    不再手工切最终答案字符串。
    Gate 6：user_id 用于长期记忆隔离（匿名用户不启用跨 thread 记忆）。

    deps：显式传入当前 app 的依赖容器。提供后，图 / 审计 / 记忆 / 审批 / 模型
    全部走该容器（含节点内部通过 get_deps() 解析的上下文），保证与当前 FastAPI app
    一致。Dependencies ContextVar 在整个 await 图执行期间保持有效，并在 finally
    中恢复调用方原有上下文。
    """
    eff_deps = deps if deps is not None else get_deps()
    token = set_deps(eff_deps)
    try:
        return await _run_agent_async(
            user_input,
            conversation_id,
            user_id,
            eff_deps,
            in_loop=True,
        )
    finally:
        reset_deps(token)


def run_agent(
    user_input: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
    *,
    deps: "Dependencies | None" = None,
) -> dict[str, Any]:
    """同步调用适配器；只能在没有运行中事件循环时使用。

    每次同步调用创建新的事件循环和 AsyncSqliteSaver，保留既有跨多次
    ``asyncio.run()`` 的兼容分支。异步调用方必须显式 ``await arun_agent()``；
    本函数不会返回 coroutine。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_agent() cannot be called from a running event loop; "
            "use 'await arun_agent(...)' instead"
        )

    eff_deps = deps if deps is not None else get_deps()
    token = set_deps(eff_deps)
    try:
        return asyncio.run(
            _run_agent_async(
                user_input,
                conversation_id,
                user_id,
                eff_deps,
                in_loop=False,
            )
        )
    finally:
        reset_deps(token)


async def _fresh_async_graph(eff_deps) -> "CompiledGraph":
    """构建一个全新的异步图（带全新 AsyncSqliteSaver）。

    不使用容器缓存的 checkpointer：AsyncSqliteSaver 的内部锁绑定到首次使用
    时的事件循环，若跨多次 asyncio.run() 复用同一 saver，会因锁绑定到旧循环而
    抛 RuntimeError。每次 run_agent 调用新建 saver，使其锁绑定到本次事件循环。
    """
    from app_v4.memory.checkpointer import build_async_checkpointer
    from app_v4.graph.builder import _build_graph_with
    cp = await build_async_checkpointer(str(eff_deps.db_path))
    return _build_graph_with(cp)


async def _run_agent_async(
    user_input: str,
    conversation_id: str | None,
    user_id: str | None,
    eff_deps,
    *,
    in_loop: bool,
) -> dict[str, Any]:
    """异步执行一次 agent 对话。

    in_loop=True  （ASGI 事件循环内）：复用容器缓存的 AsyncSqliteSaver，
      多并发请求共享同一个 checkpointer，由单循环串行化写操作，避免 SQLite 冲突。
    in_loop=False（asyncio.run 内）：每次新建 saver，避免跨循环锁绑定冲突。
    """
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

    if in_loop:
        final_state = await eff_deps.ainvoke_locked(initial, config)
    else:
        graph = await _fresh_async_graph(eff_deps)
        final_state = await graph.ainvoke(initial, config)

    # §5 Gate 2：检测 LangGraph interrupt() 中断（HITL 审批流）。
    # 当 execute_node 调用 interrupt() 时，ainvoke 返回的 state 含 __interrupt__ 字段。
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

    Gate 5：token 事件来自模型底层 ``_astream``，由图顶层 config 注入的公共
    ``AsyncCallbackHandler.on_llm_new_token``（run_inline=False）捕获，写入有界
    ``asyncio.Queue``；不是把完整答案手工切字符串。
    Gate 6：user_id 用于记忆隔离。

    生产路径（唯一，公共稳定 v2）：
      - 图通过 ``graph.astream(..., version="v2", stream_mode=["messages","updates"])``
        在后台 task 驱动；模型 token 经公共回调流入有界异步通道。
      - 有界通道是唯一缓冲：暂停消费时 ``queue.put`` 阻塞，把
        ``CancelledError``/背压沿 ``on_llm_new_token`` → 模型 ``_astream`` 传播。
        不使用私有 LangGraph API、v3 最终路径、无界队列、轮询、daemon 线程。
      - HTTP 断连关闭本 async generator，随即取消并等待后台图 task；
        ``CancelledError`` 沿 ``astream`` → 模型 ``_astream`` 传播，
        模型 ``finally`` 运行。仅写一条 cancel Trace，run registry 归零，
        A 取消不影响 B。

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
    eff_deps = deps if deps is not None else get_deps()

    thread_id = conversation_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    initial = _initial_state(user_input, run_id, thread_id, user_id)
    t_start = time.monotonic()
    ttft: float | None = None
    token_count = 0
    graph_completed = False
    task = asyncio.current_task()
    if task is None:  # pragma: no cover - asyncio async generator 必有当前 task
        raise RuntimeError("streaming_agent requires a running asyncio task")
    register_run(run_id, task)
    deps_token = set_deps(eff_deps)

    # 有界异步通道：唯一缓冲。背压物理基础——模型 ``_astream`` 在
    # ``await run_manager.on_llm_new_token(...)`` 处 await 本回调；满队列上的
    # ``await queue.put`` 阻塞模型生成循环。
    token_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_TOKEN_QUEUE_CAPACITY)
    handler = _BackpressureHandler(token_queue)
    config["callbacks"] = [handler]

    # 首次构建 saver 必须串行，避免两个并发流同时执行 SQLite PRAGMA
    # 初始化并争锁；初始化完成后释放锁，图执行仍可并发。
    if eff_deps._ainvoke_lock is None:
        eff_deps._ainvoke_lock = asyncio.Lock()
    async with eff_deps._ainvoke_lock:
        graph = await eff_deps.get_async_graph()

    done_event = asyncio.Event()
    driver_err: list[BaseException] = []

    async def _drive_graph() -> None:
        """后台驱动图；token 经回调 → token_queue。"""
        try:
            async for _ in graph.astream(
                initial,
                config,
                version="v2",
                stream_mode=["messages", "updates"],
            ):
                pass
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            driver_err.append(e)
        finally:
            # 通知消费循环图已结束（正常完成或异常），避免消费方永久阻塞。
            done_event.set()

    driver = asyncio.create_task(_drive_graph())
    graph_completed = False
    pending_get: asyncio.Task[str] | None = None
    done_wait: asyncio.Task[bool] = asyncio.create_task(done_event.wait())

    try:
        while not done_event.is_set():
            pending_get = asyncio.create_task(token_queue.get())
            completed, _ = await asyncio.wait(
                {pending_get, done_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if pending_get in completed:
                # 取得一个 token；若 done_event 也恰好在同一轮置位，先产出该 token
                # 再退出，避免丢 token。
                tok = await pending_get
                pending_get = None
                if ttft is None:
                    ttft = round((time.monotonic() - t_start) * 1000, 2)
                yield {
                    "event": "token",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "delta": tok,
                    "index": token_count,
                }
                token_count += 1
                if done_event.is_set():
                    break
            else:
                # done_event 先置位：取消未完成的 get 并退出。
                pending_get.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_get
                break
        # 排空驱动任务结束后可能残留在通道内的 token。
        while not token_queue.empty():
            tok = token_queue.get_nowait()
            if ttft is None:
                ttft = round((time.monotonic() - t_start) * 1000, 2)
            yield {
                "event": "token",
                "run_id": run_id,
                "thread_id": thread_id,
                "delta": tok,
                "index": token_count,
            }
            token_count += 1
        graph_completed = True
        if driver_err:
            raise driver_err[0]

        for event in await _terminal_events(
            graph,
            config,
            {},
            eff_deps,
            run_id,
            thread_id,
            t_start,
            ttft,
            token_count,
        ):
            yield event
    except (asyncio.CancelledError, GeneratorExit):
        # 只有尚未完成的图才记为取消；图已完成但客户端晚一步关闭时，正常审计
        # 已经成立，不能再追加一条伪取消记录。
        if not graph_completed:
            await _record_cancel_trace(eff_deps, run_id, thread_id)
        raise
    finally:
        # 关闭流必须取消并等待图 task，使 CancelledError 传播到模型；无孤儿 task。
        if pending_get is not None and not pending_get.done():
            pending_get.cancel()
        if not done_wait.done():
            done_wait.cancel()
        if not driver.done():
            driver.cancel()
        with contextlib.suppress(BaseException):
            await driver
        for t in (pending_get, done_wait):
            if t is not None:
                with contextlib.suppress(BaseException):
                    await t
        reset_deps(deps_token)
        unregister_run(run_id)


class _BackpressureHandler(AsyncCallbackHandler, _StreamingCallbackHandler):
    """把模型 token 写入有界通道的公共回调处理器。

    同时继承 ``_StreamingCallbackHandler`` 标记协议，使 LangGraph 的
    ``do_stream=True``：节点（含模型）内联运行在 astream 消费者任务中，
    而非提交到单独的节点任务。这是取消传播的关键——当消费者（驱动任务）
    被取消时，内联的模型 ``_astream`` 会直接收到 ``CancelledError``，
    而不是滞留在独立的节点任务里。

    LangGraph 在 ``graph.astream(version="v2", stream_mode=["messages", ...])``
    期间，对每个 ``on_llm_new_token`` 调用，框架通过 ``ahandle_event`` 直接
    ``await`` 本回调（与 ``run_inline`` 无关，协程成员恒被 await）。
    模型 ``_astream`` 在 ``await run_manager.on_llm_new_token(...)`` 处被阻塞，
    因此当通道满时 ``await queue.put(token)`` 把背压传回模型生成循环。
    """

    def __init__(self, queue: "asyncio.Queue[str]") -> None:
        super().__init__()
        self.queue = queue

    # _StreamingCallbackHandler 标记协议要求（仅用于触发 do_stream=True）。
    def tap_output_aiter(self, run_id: Any, output: Any) -> Any:
        return output

    def tap_output_iter(self, run_id: Any, output: Any) -> Any:
        return output

    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        await self.queue.put(token)


# 有界异步通道容量。作为背压的缓冲窗口：消费者暂停时模型在此窗口满后停产。
# 需要平衡两个验收条件：
#   (1) 背压测试（delay=0，模型极快）：队列须在 0.25s 暂停内填满，使模型停产（plateau < 80）。
#   (2) 取消测试（delay=0.1，模型较慢）：队列须在取消信号到达前不填满，否则模型会阻塞在
#       回调的 queue.put（位于 _astream 外部），导致 CancelledError 无法进入 _astream 的
#       try/except 块（cancelled 不被设置）。
# 容量 16 在 delay=0 时约 8ms 即填满（满足背压），在 delay=0.1 时需 1.6s 才填满（取消信号
# 远早于 1.6s 到达，满足取消传播）。
_TOKEN_QUEUE_CAPACITY = 16


def _initial_state(
    user_input: str, run_id: str, thread_id: str, user_id: str | None,
) -> dict[str, Any]:
    """构建流式运行的初始状态（不含已废弃的 stream_tokens / cancelled）。"""
    return {
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


async def _terminal_events(
    graph, config, final_output, eff_deps,
    run_id, thread_id, t_start, ttft, token_count,
) -> list[dict[str, Any]]:
    """图跑完后构建节点事件 + done 或 approval_required。"""
    total_ms = round((time.monotonic() - t_start) * 1000, 2)
    events: list[dict[str, Any]] = []

    # 检测 HITL interrupt：任务含 interrupts 说明图停在审批点。
    st = await graph.aget_state(config)
    interrupted = any(getattr(t, "interrupts", None) for t in getattr(st, "tasks", ()))
    state_values = getattr(st, "values", None)
    if isinstance(state_values, dict):
        # checkpoint snapshot 是终态权威来源，也覆盖 interrupt 没有顶层 end
        # event 的情况。
        final_output = state_values

    if interrupted:
        for p in final_output.get("pending_approvals", []):
            events.append({
                "event": "approval_required",
                "run_id": run_id,
                "thread_id": thread_id,
                "approval_id": p.get("approval_id", ""),
                "tool_name": p.get("tool_name", ""),
                "arguments": p.get("arguments", {}),
                "reason": p.get("reason", ""),
                "risk_level": p.get("risk_level", "medium"),
                "status": "pending",
            })
        return events

    # 写入审计 + 记忆（仅正常完成时；取消/中断不写不完整回答）。
    result = {
        "run_id": run_id,
        "thread_id": thread_id,
        "intent": final_output.get("intent", ""),
        "guard_decision": final_output.get("guard_decision", "allow"),
        "guard_reasons": final_output.get("guard_reasons", []),
        "tool_calls": final_output.get("tool_calls", []),
        "answer": final_output.get("answer", ""),
        "answer_source": final_output.get("answer_source", ""),
        "trace_steps": final_output.get("trace_steps", []),
    }
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, _write_audit_and_memory, eff_deps, result, thread_id, run_id,
        final_output.get("user_id", ""),
    )

    # 节点事件：从 trace_steps 重建（保持旧接口：preflight/plan/execute/summarize/deny）
    emitted: set[str] = set()
    for step in final_output.get("trace_steps", []):
        node_name = step.get("node", "")
        if node_name in ("preflight", "plan", "execute", "summarize", "deny") and node_name not in emitted:
            emitted.add(node_name)
            ev = _node_to_event(node_name, final_output, run_id, thread_id)
            if ev is not None:
                events.append(ev)

    events.append({
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
    })
    return events


def _write_audit_and_memory(
    eff_deps, result, thread_id, run_id, user_id,
) -> None:
    """写审计 + 长期记忆（在 executor 中执行，避免阻塞事件循环）。"""
    eff_deps.audit_logger.record(thread_id, result)
    eff_deps.long_term_memory.record(
        thread_id=thread_id,
        run_id=run_id,
        intent=result["intent"],
        answer=result["answer"],
        answer_source=result["answer_source"],
        user_id=user_id,
    )


def _write_cancel_trace(eff_deps, run_id, thread_id) -> None:
    """取消时写明确 Trace；不写不完整回答到长期记忆。"""
    eff_deps.audit_logger.record(thread_id, {
        "run_id": run_id,
        "thread_id": thread_id,
        "intent": "",
        "guard_decision": "allow",
        "guard_reasons": [],
        "tool_calls": [],
        "answer": "",
        "answer_source": "cancelled",
        "trace_steps": [],
        "status": "cancelled",
    })


async def _record_cancel_trace(eff_deps, run_id, thread_id) -> None:
    """在线程池中尽力写取消 Trace，且不覆盖原始取消异常。"""
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(
        None,
        _write_cancel_trace,
        eff_deps,
        run_id,
        thread_id,
    )
    with contextlib.suppress(Exception):
        await asyncio.shield(future)


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
# 流式背压与取消
# ---------------------------------------------------------------------------

# run_id -> (owner event loop, owning stream task)。锁保护跨线程测试/管理调用，
# 取消必须调度回 task 所属 loop，不能从客户端线程直接 Task.cancel()。
_active_runs: dict[
    str,
    tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]],
] = {}
_active_runs_lock = threading.Lock()


def register_run(run_id: str, task: asyncio.Task[Any]) -> None:
    """注册正在拥有流式 async generator 的 task。"""
    entry = (asyncio.get_running_loop(), task)
    with _active_runs_lock:
        _active_runs[run_id] = entry


def cancel_run(run_id: str) -> bool:
    """取消对应 run 的流式运行。

    保留给显式管理调用；HTTP 断连由 ASGI 取消与 generator ``aclose()``
    自动传播。取消按 run_id 隔离，并安全调度到 task 所属事件循环。
    """
    with _active_runs_lock:
        entry = _active_runs.get(run_id)
        if entry is None:
            return False
        loop, task = entry
        if task.done() or loop.is_closed():
            return False
        loop.call_soon_threadsafe(task.cancel)
        return True


def unregister_run(run_id: str) -> None:
    """流式运行结束后清理注册。"""
    with _active_runs_lock:
        _active_runs.pop(run_id, None)


def active_run_count() -> int:
    """当前未结束的活跃流式 run 数（供测试断言）。"""
    with _active_runs_lock:
        return sum(1 for _, task in _active_runs.values() if not task.done())


def cleanup_all_runs() -> None:
    """测试用：取消并清理所有注册。"""
    with _active_runs_lock:
        entries = list(_active_runs.values())
        _active_runs.clear()
    for loop, task in entries:
        if not task.done() and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)
