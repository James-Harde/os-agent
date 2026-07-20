"""AgentState — LangGraph 状态定义。

对比 app_v2 改动：
  - tool_calls 改为普通 list（修复 B04：不再错误使用 add_messages）
  - 新增 run_id / trace_steps 字段，用于 Trace 查询
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """agent 运行时完整状态。节点只返回修改的字段，框架自动合并。"""

    # 对话历史，使用 add_messages reducer 累加
    messages: Annotated[list, add_messages]

    # LLM 规划产物
    intent: str
    plan: list[dict[str, Any]]

    # 安全检查
    guard_decision: str          # "allow" / "deny"
    guard_reasons: list[str]

    # 工具执行结果（普通 list，不是 add_messages）
    tool_calls: list[dict[str, Any]]

    # 最终输出
    answer: str
    answer_source: str           # "llm_summary" / "safety_template"

    # Trace（app_v4 新增）
    run_id: str                  # 本次 Run 唯一 ID
    thread_id: str               # 当前对话 ID
    trace_steps: list[dict[str, Any]]  # 节点流转记录

    # 审批（P1 新增）
    pending_approvals: list[dict[str, Any]]  # 待审批列表

    # 记忆（P3 新增）
    memory_context: dict[str, Any]         # 长期记忆召回结果 + 渐进披露信息
    seen_plans: list[str]                  # 已出现过的 plan 签名（循环熔断用）
    loop_detected: bool                    # 本次是否触发循环熔断
