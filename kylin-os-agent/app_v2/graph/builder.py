"""图的组装 — 把节点和边连成完整的 agent。

教学要点：
  这是 LangGraph 的入口文件。你在这里：
    1. 创建 StateGraph（绑定 AgentState 定义）
    2. 注册所有节点
    3. 声明节点之间的边（固定边 + 条件边）
    4. compile() → 得到一个可调用的"图应用"

  compile() 返回的对象就是一个标准的 LangChain Runnable，
  .invoke(input) 运行一次，.stream(input) 流式运行。

  对比旧版：
    旧版 orchestrator.handle() 是一个方法，手动控制多步流程。
    新版 graph.compile() 把流程控制权交给了框架。
    你调用 app.invoke({"messages": [...]})，框架按你声明的边自动跳。
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from app_v2.graph.state import AgentState
from app_v2.graph.nodes import (
    preflight_node,
    plan_node,
    assess_plan_node,
    execute_node,
    summarize_node,
    deny_node,
)
from app_v2.graph.edges import (
    route_after_preflight,
    route_after_assess_plan,
    route_after_execute,
)
from app_v2.memory.checkpointer import build_checkpointer


def build_graph() -> StateGraph:
    """构建并编译 agent 图。

    返回编译好的、带持久化检查点的 LangGraph 应用。
    """
    graph = StateGraph(AgentState)

    # 注册节点
    # 注意：节点名（字符串）是图的"地址"，edges.py 里用这些名字来路由
    graph.add_node("preflight", preflight_node)
    graph.add_node("plan", plan_node)
    graph.add_node("assess_plan", assess_plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("deny", deny_node)

    # 声明边
    graph.add_edge(START, "preflight")                    # 入口：先做安检

    graph.add_conditional_edges(                           # 安检后：deny or continue
        "preflight", route_after_preflight,
        {"deny": "deny", "continue": "plan"},
    )
    graph.add_edge("plan", "assess_plan")                  # 计划生成后必须安检
    graph.add_conditional_edges(                           # 计划安检后：deny or execute
        "assess_plan", route_after_assess_plan,
        {"deny": "deny", "continue": "execute"},
    )
    graph.add_conditional_edges(                           # 执行后：summarize or deny
        "execute", route_after_execute,
        {"summarize": "summarize", "deny": "deny"},
    )
    graph.add_edge("summarize", END)                       # 汇总后结束
    graph.add_edge("deny", END)                            # 拒答后结束

    # 编译 + 持久化
    checkpointer = build_checkpointer()
    return graph.compile(checkpointer=checkpointer)


# 全局单例
_agent_graph = None


def get_graph():
    """获取编译后的图单例。"""
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_graph()
    return _agent_graph
