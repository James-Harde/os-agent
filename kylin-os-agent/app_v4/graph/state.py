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
    raw_plan: list[dict[str, Any]]   # LLM 原始计划（含未授权工具，供安全审计）
    plan: list[dict[str, Any]]       # 过滤后可执行计划

    # 安全检查
    guard_decision: str          # "allow" / "deny"
    guard_reasons: list[str]

    # 不可信数据 / 注入证据（preflight 检测，后续节点不得覆盖）
    untrusted_data: bool                 # 输入被识别为不可信数据（分析语境）
    prompt_injection_detected: bool      # 检测到提示词注入模式
    injection_reason_code: str           # 稳定原因码（供黑盒断言）

    # 工具执行结果（普通 list，不是 add_messages）
    tool_calls: list[dict[str, Any]]

    # 最终输出
    answer: str
    answer_source: str           # "llm_summary" / "safety_template"

    # Trace（app_v4 新增）
    run_id: str                  # 本次 Run 唯一 ID
    thread_id: str               # 当前对话 ID
    user_id: str                 # 稳定用户标识（'' 表示匿名，不启用跨 thread 记忆）
    trace_steps: list[dict[str, Any]]  # 节点流转记录

    # 审批（P1 新增）
    pending_approvals: list[dict[str, Any]]  # 待审批列表
    executed_approvals: list[str]  # 已执行过的 approval_id（防重复执行）

    # 记忆（P3 新增）
    memory_context: dict[str, Any]         # 长期记忆召回结果 + 渐进披露信息
    seen_plans: list[str]                  # 已出现过的 plan 签名（循环熔断用）
    loop_detected: bool                    # 本次是否触发循环熔断

    # 预算/熔断（Phase F 新增）
    step_count: int                        # 当前 run 已执行的节点步数
    tool_call_count: int                   # 当前 run 已调用工具总数
    budget_exceeded: bool                  # 是否已触发预算熔断

    # ---- 混合 Agent 主链：场景路由 + 只读 bounded ReAct ----
    # 场景路由（route_node 产出，编排层校验后的结果）
    route: str                             # "consult" / "knowledge" / "readonly_diagnosis" / "mutation"

    # 只读 ReAct 循环状态
    readonly_iterations: int               # 已完成的 ReAct 轮数（decide→validate→execute→scan 计 1 轮）
    readonly_tool_calls: int               # 已执行的只读工具调用数
    readonly_start_time: float             # 循环开始时间（monotonic，用于时长预算）
    readonly_last_action_key: str          # 上一轮 action 签名（工具名+参数哈希，重复/无进展检测）
    readonly_last_observation_key: str     # 上一轮 Observation 签名（无进展检测）
    readonly_no_progress_streak: int       # 连续无进展轮数
    readonly_error_streak: int             # 连续工具错误轮数
    stop_reason: str                       # ReAct 停止原因（final_answer/max_steps/max_tool_calls/
                                           #   max_duration/kill_switch/repeated_action/no_progress/
                                           #   error_limit/security_block/confirm_escalation）
    current_action: dict[str, Any]         # 当前待校验/执行的 action（结构化）
    readonly_exec_result: dict[str, Any]    # 当前轮执行结果（execute → scan 传递用）
    readonly_trace: list[dict[str, Any]]   # ReAct 专用 trace（每轮决策、校验、执行、扫描、预算）
    readonly_observations: list[dict[str, Any]]  # 累积的 Observation（供模型再次决策）
