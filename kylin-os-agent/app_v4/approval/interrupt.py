"""人机交互审批 — LangGraph interrupt() + Command(resume=...)。

修复 audit #5：从旧版 NodeInterrupt 迁移到现代 LangGraph interrupt() API，
实现批准→执行一次、拒绝→零执行、重复 resume 幂等。
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.types import interrupt, Command


class ApprovalRequest:
    """审批请求数据（传给 interrupt() 的值）。"""

    def __init__(
        self,
        approval_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> None:
        self.approval_id = approval_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "reason": self.reason,
        }


def request_approval(
    approval_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    reason: str,
) -> str:
    """在节点内调用，触发 LangGraph HITL 中断。

    首次调用暂停图执行并返回 None（中断）。
    恢复时（通过 Command(resume=decision)），返回 decision 值（"approved"/"rejected"）。

    Returns:
        "approved" 或 "rejected"（仅在 resume 时返回；首次调用返回 None 并中断）
    """
    # interrupt() 在中断点暂停；resume 时返回传入 Command(resume=...) 的值
    decision = interrupt(ApprovalRequest(approval_id, tool_name, arguments, reason).to_dict())
    return str(decision) if decision else "rejected"


ApprovalDecision = Literal["approved", "rejected"]


def resume_command(decision: ApprovalDecision) -> Command:
    """构造恢复图的 Command。"""
    return Command(resume=decision)
