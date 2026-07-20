"""图的组装 — 把节点和边连成完整的 agent。"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from app_v4.graph.state import AgentState
from app_v4.graph.nodes import (
    preflight_node, plan_node, assess_plan_node,
    execute_node, summarize_node, deny_node,
)
from app_v4.graph.edges import (
    route_after_preflight, route_after_plan, route_after_assess_plan, route_after_execute,
)
from app_v4.memory.checkpointer import build_checkpointer, build_async_checkpointer


def _build_graph_with(checkpointer) -> StateGraph:
    """用给定的 checkpointer 组装并编译 agent 图。"""
    graph = StateGraph(AgentState)

    graph.add_node("preflight", preflight_node)
    graph.add_node("plan", plan_node)
    graph.add_node("assess_plan", assess_plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("deny", deny_node)

    graph.add_edge(START, "preflight")
    graph.add_conditional_edges(
        "preflight", route_after_preflight,
        {"deny": "deny", "continue": "plan"},
    )
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
        {"summarize": "summarize", "deny": "deny"},
    )
    graph.add_edge("summarize", END)
    graph.add_edge("deny", END)

    return graph.compile(checkpointer=checkpointer)


def build_graph() -> StateGraph:
    """构建并编译同步 agent 图（带同步 checkpointer）。"""
    return _build_graph_with(build_checkpointer())


_agent_graph = None


def get_graph():
    """全局单例（同步图）。"""
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_graph()
    return _agent_graph


# ---------------------------------------------------------------------------
# 异步图（流式专用）
#
# 注意：AsyncSqliteSaver 内部持有绑定到特定事件循环的锁，因此不能在多次
# 请求间复用同一个图实例（pytest anyio 会在不同测试间切换事件循环）。
# 所以每次调用都新建图 + checkpointer，代价很小（SQLite 连接复用文件）。
# ---------------------------------------------------------------------------


async def get_async_graph():
    """每次调用都构建一个新的异步图（避免跨事件循环复用锁）。"""
    checkpointer = await build_async_checkpointer()
    return _build_graph_with(checkpointer)
