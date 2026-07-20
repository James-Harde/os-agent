"""人机交互审批 — LangGraph interrupt()。"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.errors import NodeInterrupt


class ApprovalRequest:
    def __init__(self, tool_name: str, arguments: dict[str, Any], reason: str) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {"tool_name": self.tool_name, "arguments": self.arguments, "reason": self.reason}


def request_approval(tool_name: str, arguments: dict[str, Any], reason: str) -> None:
    """在节点内调用，触发 LangGraph HITL 中断。"""
    raise NodeInterrupt(ApprovalRequest(tool_name, arguments, reason).to_dict())


ApprovalDecision = Literal["approved", "rejected"]
