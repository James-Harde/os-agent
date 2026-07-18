"""人机交互审批 —— LangChain 版（用 callback 模拟，不如 LangGraph interrupt()）。

教学要点：
  LangChain 没有真正的"中断等用户决定"原语。
  业界常见的几种 hack 方式：

    1. 用 callback：工具执行前触发回调，抛异常让 agent 报错
       （丑，且不标准）
    2. 用 ConversationalAgent 的"需要确认"输出格式，解析出来人工处理
       （复杂，依赖 prompt 配合）
    3. 两阶段：先跑 agent 拿到工具列表 → 前端展示让用户确认 → 再跑
       （能 work，但打断了 agent 的自然循环）

  对比 LangGraph 的 interrupt()：
    LangGraph: 原生支持，一个函数调用搞定
    LangChain:  需要 hack，没有标准方案

  这是 LangGraph 相对于 LangChain 的核心优势之一。
  如果是生产级 agent（需要人审、需要中断恢复），LangGraph 几乎是唯一选择。
"""

from __future__ import annotations

from typing import Any


class HumanApprovalRequired(Exception):
    """工具执行前需要人工确认时抛出。

    在 LangChain AgentExecutor 中，这不是标准做法。
    标准做法是正常返回工具结果。
    但为了"教学习"保留这个类，展示 LangChain 做不到的事。
    """

    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        super().__init__(f"工具 '{tool_name}' 需要人工审批: {arguments}")


def check_tool_permission(tool_name: str) -> None:
    """根据工具名决定是否需要审批。

    在 v3 中，所有 SAFE_TOOLS 都是安全的，不会触发此函数。
    如果未来要加危险工具（如 service_restart），在这里决定拦截策略。
    """
    confirm_tools = {"service_restart", "file_delete"}
    if tool_name in confirm_tools:
        raise HumanApprovalRequired(tool_name, {})
