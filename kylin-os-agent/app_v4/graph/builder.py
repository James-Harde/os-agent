"""图的组装 — 把节点和边连成完整的 agent。

混合 Agent 主链拓扑：

  START → preflight → route → 分流：
    ├─ consult → direct_answer → END
    ├─ knowledge → plan → assess_plan → execute → [approval_interrupt]* → summarize → END
    ├─ readonly_diagnosis → readonly_decide ⇄ validate_action → readonly_execute → scan_observation
    │     └─ confirm → confirm_escalation → approval_interrupt → summarize → END
    │     └─ final/stop → readonly_stop → END
    └─ mutation → plan → assess_plan → execute → [approval_interrupt]* → summarize/deny → END
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from app_v4.graph.state import AgentState
from app_v4.graph.nodes import (
    preflight_node, plan_node, assess_plan_node,
    execute_node, approval_interrupt_node, summarize_node, deny_node,
    route_node, direct_answer_node,
)
from app_v4.graph.readonly_react import (
    readonly_decide_node, validate_action_node, readonly_execute_node,
    scan_observation_node, readonly_stop_node, confirm_escalation_node,
)
from app_v4.graph.edges import (
    route_after_preflight, route_after_route, route_after_plan, route_after_assess_plan,
    route_after_execute, route_after_approval_interrupt,
    route_after_readonly_decide, route_after_validate_action,
    route_after_readonly_execute, route_after_scan_observation,
    route_after_confirm_escalation,
)
from app_v4.memory.checkpointer import build_checkpointer, build_async_checkpointer


def _build_graph_with(checkpointer) -> StateGraph:
    """用给定的 checkpointer 组装并编译 agent 图。"""
    graph = StateGraph(AgentState)

    # --- 外层 Workflow 节点 ---
    graph.add_node("preflight", preflight_node)
    graph.add_node("route", route_node)
    graph.add_node("direct_answer", direct_answer_node)

    # --- 知识/副作用路径（Plan → 安全审核 → HITL → 执行 → 总结）---
    graph.add_node("plan", plan_node)
    graph.add_node("assess_plan", assess_plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("approval_interrupt", approval_interrupt_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("deny", deny_node)

    # --- 只读 bounded ReAct 节点 ---
    graph.add_node("readonly_decide", readonly_decide_node)
    graph.add_node("validate_action", validate_action_node)
    graph.add_node("readonly_execute", readonly_execute_node)
    graph.add_node("scan_observation", scan_observation_node)
    graph.add_node("readonly_stop", readonly_stop_node)
    graph.add_node("confirm_escalation", confirm_escalation_node)

    # --- 边 ---
    graph.add_edge(START, "preflight")
    graph.add_conditional_edges(
        "preflight", route_after_preflight,
        {"deny": "deny", "route": "route"},
    )

    # 场景路由分流
    graph.add_conditional_edges(
        "route", route_after_route,
        {
            "direct_answer": "direct_answer",
            "plan": "plan",
            "readonly_decide": "readonly_decide",
            "confirm_escalation": "confirm_escalation",
        },
    )

    # consult → END
    graph.add_edge("direct_answer", END)

    # knowledge / mutation → plan 路径
    graph.add_conditional_edges(
        "plan", route_after_plan,
        {"deny": "deny", "continue": "assess_plan"},
    )
    graph.add_conditional_edges(
        "assess_plan", route_after_assess_plan,
        {"deny": "deny", "continue": "execute"},
    )
    graph.add_conditional_edges(
        "execute", route_after_execute,
        {"summarize": "summarize", "deny": "deny", "approval_interrupt": "approval_interrupt"},
    )
    graph.add_conditional_edges(
        "approval_interrupt", route_after_approval_interrupt,
        {"summarize": "summarize", "approval_interrupt": "approval_interrupt"},
    )
    graph.add_edge("summarize", END)
    graph.add_edge("deny", END)

    # --- 只读 bounded ReAct 循环 ---
    graph.add_conditional_edges(
        "readonly_decide", route_after_readonly_decide,
        {"readonly_stop": "readonly_stop", "validate_action": "validate_action"},
    )
    graph.add_conditional_edges(
        "validate_action", route_after_validate_action,
        {
            "readonly_execute": "readonly_execute",
            "readonly_stop": "readonly_stop",
            "confirm_escalation": "confirm_escalation",
        },
    )
    graph.add_edge("readonly_execute", "scan_observation")
    graph.add_conditional_edges(
        "scan_observation", route_after_scan_observation,
        {"readonly_decide": "readonly_decide", "readonly_stop": "readonly_stop"},
    )
    graph.add_edge("readonly_stop", END)

    # confirm 升级 → 进入 HITL 审批链
    graph.add_conditional_edges(
        "confirm_escalation", route_after_confirm_escalation,
        {"approval_interrupt": "approval_interrupt"},
    )

    return graph.compile(checkpointer=checkpointer)


def build_graph() -> StateGraph:
    """构建并编译同步 agent 图（带同步 checkpointer）。"""
    return _build_graph_with(build_checkpointer())


def get_graph():
    """向后兼容入口：路由到当前活动容器。"""
    from app_v4.container import get_deps
    return get_deps().get_graph()


# ---------------------------------------------------------------------------
# 异步图（流式专用）
#
# 注意：AsyncSqliteSaver 内部持有绑定到特定事件循环的锁，因此不能在多次
# 请求间复用同一个图实例（pytest anyio 会在不同测试间切换事件循环）。
# 所以每次调用都新建图 + checkpointer，代价很小（SQLite 连接复用文件）。
# ---------------------------------------------------------------------------


async def get_async_graph():
    """每次调用都构建一个新的异步图（避免跨事件循环复用锁）。"""
    from app_v4.container import get_deps
    return await get_deps().get_async_graph()
