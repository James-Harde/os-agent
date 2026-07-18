"""节点函数 — LangGraph 图的原子操作。

教学要点：
  每个节点就是一个纯函数：接收 AgentState，返回被修改的字段 dict。
  节点之间不直接调用——它们只通过 state 和图的边间接通信。

  这是和旧版最本质的差异：
    旧版 orchestrator.py 里，preflight → plan → execute 是串行硬编码的。
    现在每个步骤拆成独立节点，谁连谁由 edges.py 声明。
    你想加一步（比如"Plan 前先做权限检查"），只需加一个节点 + 连一条边，
    不需要改动已有节点代码。

  节点职责单一原则：
    - preflight_node：安全检查（不碰 LLM）
    - plan_node：调 LLM 生成意图和工具计划
    - execute_node：执行计划里的每个工具
    - summarize_node：把结果汇总成自然语言回答
    - deny_node：生成拒绝回答（命中安全规则时走这里）
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app_v2.graph.state import AgentState
from app_v2.model.chat_model import get_chat_model
from app_v2.safety.guard import SafetyGuard
from app_v2.tools.registry import get_tool_names, TOOL_BY_NAME


# ---------------------------------------------------------------------------
# 节点 1: 安全预检（在任何 LLM 调用之前）
# ---------------------------------------------------------------------------
def preflight_node(state: AgentState) -> dict[str, Any]:
    """检查用户输入是否命中高危规则。

    如果命中，直接把 guard_decision 设为 "deny"，
    条件边会路由到 deny_node 而不是 plan_node。
    """
    guard = SafetyGuard()
    user_input = _latest_user_message(state)
    result = guard.check_input(user_input)

    return {
        "guard_decision": "deny" if result["risk_level"] == "high" and not result["is_analysis_context"] else "allow",
        "guard_reasons": result["reasons"],
    }


# ---------------------------------------------------------------------------
# 节点 2: LLM 规划（调 LLM 理解意图 + 生成工具计划）
# ---------------------------------------------------------------------------
def plan_node(state: AgentState) -> dict[str, Any]:
    """调用 LLM 生成执行计划。

    注意：plan_node 不执行任何工具。它只输出 plan。
    这遵循"计划和执行分离"原则——LLM 只在想，不在做。
    这样的好处是可以在 plan 之后插入安全检查（assess_plan），
    如果计划里包含未授权工具，可以在此拦截。
    """
    model = get_chat_model()
    user_input = _latest_user_message(state)
    allowed_tools = get_tool_names()

    system_prompt = (
        "你是麒麟/Linux 安全运维 Agent 的大脑。"
        "只使用 allowed_tools 中的工具；不要输出 shell 命令。"
        "只规划只读工具调用。返回 JSON。"
    )
    user_prompt = json.dumps({
        "allowed_tools": sorted(allowed_tools),
        "user_input": user_input,
        "schema": {
            "intent": "string",
            "plan": [{"tool": "name", "arguments": {}, "reason": "why"}],
        },
    }, ensure_ascii=False)

    response = model.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    parsed = _extract_json(response.content)
    intent = parsed.get("intent", "general_help")
    plan = [
        {"tool": s["tool"], "arguments": s.get("arguments", {}),
         "reason": s.get("reason", "")}
        for s in parsed.get("plan", [])
        if s.get("tool") in allowed_tools
    ]

    return {"intent": intent, "plan": plan}


# ---------------------------------------------------------------------------
# 节点 3: 计划安全检查
# ---------------------------------------------------------------------------
def assess_plan_node(state: AgentState) -> dict[str, Any]:
    """验证 LLM 生成的计划是否只包含白名单工具。

    这是第二道防线：即使 LLM 试图规划一个不在白名单里的工具，
    这里会拦截，不会走到 execute_node。
    """
    guard = SafetyGuard()
    allowed = get_tool_names()
    result = guard.check_plan(state.get("plan", []), allowed)

    return {
        "guard_decision": "deny" if result["risk_level"] == "high" else "allow",
        "guard_reasons": result["reasons"],
    }


# ---------------------------------------------------------------------------
# 节点 4: 工具执行
# ---------------------------------------------------------------------------
def execute_node(state: AgentState) -> dict[str, Any]:
    """按计划调用每个工具。对比旧版：
    旧版 orchestrator 里 for step in plan → ToolRegistry.call()
    新版逻辑相同，但每个工具调用完会检查输出是否有注入，
    且执行结果累加到 tool_calls（add_messages reducer 语义）。
    """
    guard = SafetyGuard()
    results = []

    for step in state.get("plan", []):
        tool_name = step["tool"]
        tool = TOOL_BY_NAME.get(tool_name)
        if tool is None:
            results.append({
                "tool_name": tool_name,
                "status": "error",
                "error": f"unknown tool: {tool_name}",
                "result": {},
            })
            continue

        # LangChain @tool 对象用 invoke() 调用
        try:
            result = tool.invoke(step["arguments"])
            status = "ok"
        except Exception as exc:
            result = {"error": str(exc)}
            status = "error"

        # 工具输出安全扫描（防止注入）
        scan = guard.scan_untrusted_output({"tool_name": tool_name, "result": result})

        results.append({
            "tool_name": tool_name,
            "arguments": step["arguments"],
            "reason": step.get("reason", ""),
            "status": status,
            "result": result,
            "output_scan": scan,
        })

    return {"tool_calls": results}


# ---------------------------------------------------------------------------
# 节点 5: LLM 汇总
# ---------------------------------------------------------------------------
def summarize_node(state: AgentState) -> dict[str, Any]:
    """根据工具调用结果，让 LLM 生成最终回答。"""
    model = get_chat_model()
    user_input = _latest_user_message(state)

    context = json.dumps({
        "user_input": user_input,
        "intent": state.get("intent"),
        "tool_calls": [
            {"tool_name": c.get("tool_name"), "status": c.get("status"),
             "result": c.get("result")}
            for c in state.get("tool_calls", [])
        ],
    }, ensure_ascii=False)

    response = model.invoke([
        SystemMessage(content=(
            "你是安全运维 Agent 的总结模块。请基于工具结果给出中文结论。"
            "最多 4 行：结论、依据、建议、安全状态。"
        )),
        HumanMessage(content=context),
    ])

    return {
        "answer": response.content.strip(),
        "answer_source": "llm_summary",
        "guard_decision": "allow",
    }


# ---------------------------------------------------------------------------
# 节点 6: 拒答（命中安全规则时走这里）
# ---------------------------------------------------------------------------
def deny_node(state: AgentState) -> dict[str, Any]:
    """生成拒绝回答。不调用任何工具。

    对应旧版 orchestrator 里 preflight/explain_denial 的逻辑。
    """
    reasons = "；".join(state.get("guard_reasons", [])[:3]) or "命中安全规则"
    return {
        "answer": "\n".join([
            f"已拒绝自动执行：该请求属于高风险操作。",
            f"原因：{reasons}。",
            "建议：先做只读诊断，确认路径和影响范围后走人工审批。",
            "状态：未调用任何执行类工具，已写入审计日志。",
        ]),
        "answer_source": "safety_template",
    }


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
def _latest_user_message(state: AgentState) -> str:
    """从 messages 列表里取最后一条 HumanMessage。"""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def _extract_json(content: str) -> dict[str, Any]:
    """从 LLM 返回文本里提取 JSON（兼容模型输出带 Markdown 的情况）。"""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"intent": "general_help", "plan": []}
