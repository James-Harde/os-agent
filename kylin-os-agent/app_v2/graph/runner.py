"""图运行的封装 — 给 FastAPI 入口调。

教学要点：
  runner 是 LangGraph 图和 FastAPI 之间的"翻译层"。
  FastAPI 收到 HTTP 请求 → 调 runner.run() → 框架把输入喂进图 →
  图在各节点之间跳 → 最终 state 返回 → runner 提取 answer 给 HTTP 响应。

  关键：thread_id = conversation_id
  LangGraph 用 thread_id 区分不同对话的检查点存档。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from app_v2.graph.builder import get_graph


def run_agent(
    user_input: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """运行一次 agent 对话。

    Args:
        user_input: 用户输入的自然语言。
        conversation_id: 对话 ID（None 则开启新对话）。

    Returns:
        dict 包含 answer/intent/guard_decision/tool_calls 等。
    """
    graph = get_graph()

    # thread_id 对应 LangGraph 检查点里的"线程"
    config = {"configurable": {"thread_id": conversation_id or "default"}}

    # 输入：HumanMessage 进入 state.messages
    # LangGraph 会自动通过 add_messages reducer 累加
    initial = {"messages": [HumanMessage(content=user_input)]}

    # invoke() 让框架接管：按边在各节点间跳，直到走到 END
    final_state = graph.invoke(initial, config)

    return {
        "conversation_id": config["configurable"]["thread_id"],
        "intent": final_state.get("intent", ""),
        "guard_decision": final_state.get("guard_decision", "allow"),
        "guard_reasons": final_state.get("guard_reasons", []),
        "tool_calls": final_state.get("tool_calls", []),
        "answer": final_state.get("answer", ""),
        "answer_source": final_state.get("answer_source", ""),
    }
