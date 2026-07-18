"""人机交互审批 — LangGraph interrupt() 版。

教学要点：
  LangGraph 有一个核心原语：interrupt()
  节点执行到 interrupt() 时，整个 agent 暂停，控制权交还给人类。
  人类做出决定后，把决定传回 agent，agent 从断点继续执行。

  这比旧版手写 approval 表优雅很多：
    旧版：创建 approval 行 → 记录 blocked → 用户调 API approve → 重新触发执行
    新版：节点里 interrupt(request) → 用户看到审批卡片 → 传回 Approve/Reject
          → 框架自动把结果塞回 state → 接着跑

  LangGraph 的 interrupt() 本质就是"可恢复的断点"。
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.errors import NodeInterrupt


class ApprovalRequest:
    """审批请求的数据结构。传给 interrupt() 的 payload。"""

    def __init__(self, tool_name: str, arguments: dict[str, Any], reason: str) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "reason": self.reason,
        }


def request_approval(tool_name: str, arguments: dict[str, Any], reason: str) -> None:
    """在节点内调用此函数，触发 LangGraph 的人机交互中断。

    调用后：
      1. agent 立即暂停
      2. 前端/Web UI 看到 ApprovalRequest 数据，展示审批卡片
      3. 用户点 Approve 或 Reject
      4. LangGraph 把用户决定传回，节点继续执行
    """
    raise NodeInterrupt(ApprovalRequest(tool_name, arguments, reason).to_dict())


ApprovalDecision = Literal["approved", "rejected"]
