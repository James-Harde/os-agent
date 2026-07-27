"""条件边 — LangGraph 的路由决策。

对比 app_v2 改动：
  - 修复 B07：空计划不再被错判为高风险，走 summarize 生成"暂无可执行工具"回答
  - P3：plan 节点后加入循环熔断路由（loop_detected 直接走 deny）
  - 混合 Agent 主链：preflight 后加入场景路由（consult/knowledge/readonly_diagnosis/mutation）
  - 只读 bounded ReAct 循环路由
"""

from app_v4.graph.state import AgentState


def route_after_preflight(state: AgentState) -> str:
    """preflight 节点之后：deny 走 deny，否则走 route（场景路由）。"""
    return "deny" if state.get("guard_decision") == "deny" else "route"


def route_after_route(state: AgentState) -> str:
    """场景路由之后：按 route 字段分流。

    - consult → direct_answer（直接回答）
    - knowledge → plan（走 RAG 路径，模型会规划 rag_search）
    - readonly_diagnosis → readonly_decide（进入 bounded ReAct）
    - mutation → plan（走 Plan → 安全审核 → HITL → 冻结参数执行）
    """
    route = state.get("route", "consult")
    if route == "consult":
        return "direct_answer"
    if route == "knowledge":
        return "plan"
    if route == "readonly_diagnosis":
        return "readonly_decide"
    if route == "mutation":
        return "plan"
    # 未知 route 降级为 consult
    return "direct_answer"


def route_after_plan(state: AgentState) -> str:
    """plan 节点之后：循环熔断走 deny，否则走 assess_plan。"""
    return "deny" if state.get("loop_detected") else "continue"


def route_after_assess_plan(state: AgentState) -> str:
    """assess_plan 节点之后：deny 走 deny，否则走 execute。"""
    return "deny" if state.get("guard_decision") == "deny" else "continue"


def route_after_execute(state: AgentState) -> str:
    """execute 节点之后：有待审批项则进入 approval_interrupt，有工具调用则 summarize，否则走 deny。"""
    if state.get("pending_approvals"):
        return "approval_interrupt"
    tool_calls = state.get("tool_calls", [])
    return "summarize" if tool_calls else "deny"


def route_after_approval_interrupt(state: AgentState) -> str:
    """approval_interrupt 节点之后：

    - 仍有 pending（未决策）的审批单 → 继续 interrupt 等待
    - 全部已决策 → summarize
    """
    pending = state.get("pending_approvals", [])
    store = None
    for p in pending:
        # 延迟导入避免循环
        if store is None:
            from app_v4.approval.store import get_approval_store
            store = get_approval_store()
        record = store.get(p["approval_id"])
        if record is None or record["status"] == "pending":
            return "approval_interrupt"  # 仍有未决策的，继续等待
    return "summarize"


# ---------------------------------------------------------------------------
# 只读 bounded ReAct 循环路由
# ---------------------------------------------------------------------------
def route_after_readonly_decide(state: AgentState) -> str:
    """readonly_decide 之后：

    - 模型返回 final answer → readonly_stop（生成最终回答）
    - 模型返回 tool call → validate_action
    - 已设置 stop_reason（预算/熔断） → readonly_stop
    """
    # 如果 decide 节点已触发停止条件
    stop_reason = state.get("stop_reason")
    if stop_reason and stop_reason != "confirm_escalation":
        return "readonly_stop"

    action = state.get("current_action", {})
    action_type = action.get("action", "")

    if action_type == "final":
        return "readonly_stop"
    if action_type == "tool":
        return "validate_action"

    # 无法解析 → 停止
    return "readonly_stop"


def route_after_validate_action(state: AgentState) -> str:
    """validate_action 之后：

    - confirm 工具 → confirm_escalation（退出 ReAct，进入 HITL）
    - 设置了 stop_reason（安全阻断/预算） → readonly_stop
    - 校验通过 → readonly_execute
    """
    stop_reason = state.get("stop_reason")
    if stop_reason == "confirm_escalation":
        return "confirm_escalation"
    if stop_reason:
        return "readonly_stop"

    action = state.get("current_action", {})
    if action.get("tool"):
        return "readonly_execute"

    return "readonly_stop"


def route_after_readonly_execute(state: AgentState) -> str:
    """readonly_execute 之后：总是进入 scan_observation。"""
    return "scan_observation"


def route_after_scan_observation(state: AgentState) -> str:
    """scan_observation 之后：

    - 已设置 stop_reason → readonly_stop
    - 否则 → 回到 readonly_decide（继续循环）
    """
    stop_reason = state.get("stop_reason")
    if stop_reason and stop_reason != "confirm_escalation":
        return "readonly_stop"

    # 检查是否达到迭代上限（在 validate_action 也会检查，这里做兜底）
    iteration = state.get("readonly_iterations", 0)
    settings = None
    try:
        from app_v4.container import get_deps
        settings = get_deps().settings
    except Exception:
        pass
    max_iters = getattr(settings, "max_readonly_iterations", 5) if settings else 5
    if iteration >= max_iters:
        return "readonly_stop"

    return "readonly_decide"


def route_after_confirm_escalation(state: AgentState) -> str:
    """confirm_escalation 之后：进入 approval_interrupt（HITL 审批）。"""
    return "approval_interrupt"
