"""条件边 — LangGraph 的路由决策。

教学要点：

  LangGraph 的边分两种：
    1. 固定边 (add_edge)：A → B（无条件，A 跑完一定去 B）
    2. 条件边 (add_conditional_edges)：A → ?（看 state 决定去哪）

  条件边由一个"路由函数"驱动，它读 state 返回下一个节点的名字。
  这是旧版 orchestrator.py 里大量 if/else 的替代物。

  旧版：
    if guard_decision == "deny":
        answer = explain_denial(...)
    else:
        for step in plan:
            ...

  新版：
    # 声明式：guard_decision == "deny" 就走 "deny"，否则走 "plan"
    graph.add_conditional_edges("preflight", route_after_preflight, {
        "deny": "deny_node",
        "continue": "plan_node",
    })

  好处：路由逻辑从"执行代码"变成"声明规则"，
  改规则只改这一行，不用在 200 行 orchestrator 里找 if/else。
"""

from app_v2.graph.state import AgentState


def route_after_preflight(state: AgentState) -> str:
    """preflight 节点之后走哪条路。"""
    return "deny_node" if state.get("guard_decision") == "deny" else "plan_node"


def route_after_assess_plan(state: AgentState) -> str:
    """assess_plan 节点之后走哪条路。"""
    return "deny_node" if state.get("guard_decision") == "deny" else "execute_node"


def route_after_execute(state: AgentState) -> str:
    """execute 节点之后：如果有工具调用就汇总，没有就拒答。"""
    tool_calls = state.get("tool_calls", [])
    return "summarize_node" if tool_calls else "deny_node"
