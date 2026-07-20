"""条件边 — LangGraph 的路由决策。

对比 app_v2 改动：
  - 修复 B07：空计划不再被错判为高风险，走 summarize 生成"暂无可执行工具"回答
  - P3：plan 节点后加入循环熔断路由（loop_detected 直接走 deny）
"""

from app_v4.graph.state import AgentState


def route_after_preflight(state: AgentState) -> str:
    """preflight 节点之后：deny 走 deny，否则走 plan。"""
    return "deny" if state.get("guard_decision") == "deny" else "continue"


def route_after_plan(state: AgentState) -> str:
    """plan 节点之后：循环熔断走 deny，否则走 assess_plan。"""
    return "deny" if state.get("loop_detected") else "continue"


def route_after_assess_plan(state: AgentState) -> str:
    """assess_plan 节点之后：deny 走 deny，否则走 execute。"""
    return "deny" if state.get("guard_decision") == "deny" else "continue"


def route_after_execute(state: AgentState) -> str:
    """execute 节点之后：有工具调用则 summarize，否则走 deny。"""
    tool_calls = state.get("tool_calls", [])
    return "summarize" if tool_calls else "deny"
