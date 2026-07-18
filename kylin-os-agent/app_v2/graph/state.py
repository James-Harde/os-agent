"""AgentState — LangGraph 的状态定义。

教学要点：
  LangGraph 的"状态"就是节点之间传递的数据结构。
  你声明一次，所有节点共享，节点只返回自己改变的部分（merge 语义）。

  对比旧版：
    旧版 orchestrator.py 里手动在函数之间传来传去（intent, plan, tool_calls...）
    现在框架帮你管，每个节点只返回它改的那几个字段，框架自动合并。
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from langgraph.messages import HumanMessage, AIMessage
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """整个 agent 运行时的完整状态。

    每个节点函数接收这个 state，返回一个 dict（只包含它修改的字段）。
    LangGraph 自动把返回的 dict 合并进 state。

    字段说明：
      messages           — 对话历史，用 add_messages reducer 实现"累加"语义
                          （新消息 append，不覆盖旧的）
      intent             — LLM 判断的用户意图
      plan               — LLM 生成的工具调用计划 [{tool, arguments, reason}]
      guard_decision     — 安全检查结果 "allow" / "deny"
      guard_reasons      — 安全拦截原因
      tool_calls         — 工具执行结果列表（累加）
      answer             — 最终给用户的回答
      answer_source      — "llm_summary" / "safety_template"
    """

    messages: Annotated[list, add_messages]
    intent: str
    plan: list[dict[str, Any]]
    guard_decision: str
    guard_reasons: list[str]
    tool_calls: Annotated[list[dict[str, Any]], add_messages]  # 复用累加语义
    answer: str
    answer_source: str
