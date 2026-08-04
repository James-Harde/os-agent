"""LangChain ChatModel 封装。

提供 fake model 用于测试（不依赖真实 LLM），通过环境变量切换：
  APP_V4_USE_FAKE_MODEL=true  → 使用确定性假模型
  默认 → 使用真实 ChatOpenAI
"""

from __future__ import annotations

import os
import json
import re
import asyncio
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI


def build_chat_model(settings=None) -> BaseChatModel:
    """构建 ChatModel。

    优先级：
      1. 显式传入的 settings 对象
      2. 环境变量（向后兼容）
    """
    from app_v4.settings import load_settings, Settings
    s: Settings = settings or load_settings()

    if s.use_fake_model:
        return _FakeChatModel()
    return ChatOpenAI(
        base_url=s.openai_compatible_base_url.rstrip("/"),
        api_key=s.openai_compatible_api_key,
        model=s.openai_compatible_model,
        timeout=s.openai_compatible_timeout,
        temperature=0.1,
    )


def get_chat_model() -> BaseChatModel:
    """向后兼容入口：路由到当前活动容器。"""
    from app_v4.container import get_deps
    return get_deps().model


async def model_invoke_streaming(
    model: BaseChatModel, messages: list[BaseMessage], state: dict,
) -> str:
    """异步调用模型并返回完整文本。

    Gate 5：节点通过 ``model.ainvoke()`` 调用模型。这里显式传入 ``stream=True``，
    使模型走 ``_astream`` 路径并把每个 token 通过 ``on_llm_new_token`` 回调送出
    （由 ``graph/runner.py`` 的 ``_BackpressureHandler`` 收集进有界通道），
    同时 ``ainvoke`` 仍会把所有 chunk 聚合为完整回答返回。不使用任何私有
    LangGraph/LangChain 标记协议——``stream=True`` 是 ``ainvoke`` 的公开参数，
    对不注册 token 回调的调用方行为等价于非流式（仅内部走 ``_astream`` 聚合）。
    这里不手工切分答案，也不在模型通用封装里耦合背压逻辑——那些职责隔离在
    ``graph/runner.py`` 的 streaming 模块。

    兼容分支：
      - 有 ``ainvoke`` 的模型（ChatOpenAI、FakeChatModel）走异步调用（流式聚合）。
      - 仅有 ``astream`` 的旧测试替身走异步流拼接。
      - 仅有同步 ``invoke`` 的模型在线程中执行以避免阻塞事件循环。
    """
    if hasattr(model, "ainvoke"):
        response = await model.ainvoke(messages, stream=True)
        return _message_text(response)
    if hasattr(model, "astream"):
        chunks: list[str] = []
        async for chunk in model.astream(messages):
            text = _message_text(chunk)
            if text:
                chunks.append(text)
        return "".join(chunks)
    # 兜底：旧测试模型仅有同步 invoke，在线程中执行以避免阻塞事件循环。
    response = await asyncio.to_thread(model.invoke, messages)
    return _message_text(response)


def _message_text(message: Any) -> str:
    """兼容字符串内容与 v3 content-block 内容，提取模型文本。"""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


class _FakeChatModel(BaseChatModel):
    """确定性假模型，用于测试。不调用任何外部 API。

    设计原则（避免审计 #8 "工具名猜意图 + 参数硬编码"）：
      - 意图判断只基于用户消息内容（不扫描 allowed_tools / 工具描述），
        避免"你好"误判为磁盘。
      - 参数（端口号、limit、path）从用户输入中实际解析，不硬编码 8080。
      - 支持追问上下文：当输入含"那...呢/也看看"等指代词时，
        从 thread 历史中恢复上一轮的工具类别，仅替换新参数。
      - astream 产出逐 token 事件（按字/词切分），供 SSE token 流测试。
    """

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatResult, ChatGeneration

        del stop, run_manager, kwargs
        content = _fake_response(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any):
        """按字/词逐 token yield AIMessageChunk（确定性，供 SSE token 流）。"""
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        content = _fake_response(messages)
        # 按字/词切分（中文单字，英文按空格），模拟真实 token 流
        tokens = _tokenize_for_stream(content)
        for i, tok in enumerate(tokens):
            # 第一个 token 前可注入微小延迟模拟 TTFT（测试中忽略）
            yield ChatGenerationChunk(message=AIMessageChunk(content=tok))

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"


def _tokenize_for_stream(text: str) -> list[str]:
    """把文本切分为 token 流（中文单字，英文/数字按空格切）。"""
    tokens: list[str] = []
    buf = ""
    for ch in text:
        if "一" <= ch <= "鿿":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch.isalnum() or ch in "_-":
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            if not ch.isspace():
                tokens.append(ch)
            else:
                tokens.append(" ")
    if buf:
        tokens.append(buf)
    # 合并连续空格
    merged: list[str] = []
    for t in tokens:
        if t == " " and merged and merged[-1] == " ":
            continue
        merged.append(t)
    return merged


def _extract_user_input_from_prompt(prompt: str) -> str:
    """从规划请求 JSON 中提取 user_input 字段（容错：失败返回空串）。"""
    from app_v4.graph.nodes import _extract_json

    data = _extract_json(prompt)
    if isinstance(data, dict):
        return data.get("user_input", "")
    return ""


def _port_from_text(text: str) -> int | None:
    """从文本中解析端口号（取第一个看起来像端口的 2-5 位数字）。

    设计：模型只负责"忠实提取用户提到的数字"，范围校验（1-65535）是工具
    port_lookup 的职责。这样"查询端口 99999"会被模型提取为 99999，
    再由工具返回 validation error，避免模型把非法端口静默改成 8080。
    """
    nums = re.findall(r"\b(\d{2,5})\b", text)
    for n in nums:
        v = int(n)
        if 100 <= v <= 99999:  # 端口候选范围（宽松，校验留给工具）
            return v
    return None


def _human_contents(messages: list[BaseMessage]) -> list[str]:
    """提取所有 HumanMessage 内容（作为 thread 上下文）。"""
    from langchain_core.messages import HumanMessage
    return [m.content for m in messages if isinstance(m, HumanMessage)]


def _count_tool_messages(messages: list[BaseMessage]) -> int:
    """统计消息列表中 ToolMessage 的数量（用于 ReAct 多轮决策）。"""
    from langchain_core.messages import ToolMessage
    return sum(1 for m in messages if isinstance(m, ToolMessage))


def _fake_response(messages: list[BaseMessage]) -> str:
    """根据消息列表返回确定性响应。

    第一条消息通常是 system prompt；最后一条是当前规划请求（JSON）。
    支持通过 thread 历史（前面的 HumanMessage）解析追问上下文。

    支持四种模式：
      1. 场景路由（"场景路由器"在 system prompt 中）→ 返回 {route, reason}
      2. ReAct 决策（"只读诊断模块"在 system prompt 中）→ 返回 {action, tool/answer}
      3. 规划请求（allowed_tools 在 prompt 中）→ 返回 {intent, plan}
      4. 总结请求 → 返回总结文本
    """
    from langchain_core.messages import SystemMessage

    first_content = messages[0].content if messages else ""
    last = messages[-1].content if messages else ""

    # --- 场景路由 ---
    if "场景路由器" in first_content:
        return _fake_route(messages)

    # --- ReAct 决策 ---
    if "只读诊断模块" in first_content:
        return _fake_react_decide(messages, last)

    # 判断是规划请求还是总结请求：system prompt 或最后一条含 allowed_tools
    is_planning = "allowed_tools" in last or "plan" in str(last).lower()[:200]

    if not is_planning:
        # 总结请求
        return "根据工具结果：系统运行正常，未发现异常。建议：持续监控关键指标。"

    # --- 规划请求 ---
    user_input = _extract_user_input_from_prompt(last)
    lowered = user_input.lower()
    history = _human_contents(messages[:-1])  # 历史 HumanMessage（不含当前）

    # 追问检测：含指代词且含新参数 → 恢复上一工具类别，替换参数
    follow_up_markers = ("那", "那 ", "也", "还", "另外", "再")
    is_follow_up = any(m in user_input for m in follow_up_markers)

    # ---- 按优先级识别意图（基于 user_input 关键词 + 解析的真实参数）----

    # 知识库检索（优先级高于端口：含"知识库/FAQ"等时即使同时含端口词也走 RAG）
    if any(k in user_input for k in ("知识库", "知识", "FAQ", "faq")):
        query = user_input or "运维知识"
        return json.dumps({
            "intent": "knowledge_query",
            "plan": [{"tool": "rag_search", "arguments": {"query": query}, "reason": "从知识库检索相关知识"}],
        }, ensure_ascii=False)

    # 端口查询（含追问"那 5432 呢"）
    if "端口" in user_input or "port" in lowered or _port_from_text(user_input) is not None:
        port = _port_from_text(user_input) or 8080
        return json.dumps({
            "intent": "port_analysis",
            "plan": [{"tool": "port_lookup", "arguments": {"port": port}, "reason": f"查询端口 {port} 占用"}],
        }, ensure_ascii=False)

    # 追问恢复：历史是端口查询，当前仅含"那...呢"不再含端口词
    if is_follow_up and history:
        last_user = history[-1] if history else ""
        if "端口" in last_user or "port" in last_user.lower():
            port = _port_from_text(user_input) or 8080
            return json.dumps({
                "intent": "port_analysis",
                "plan": [{"tool": "port_lookup", "arguments": {"port": port}, "reason": f"追问：查询端口 {port}"}],
            }, ensure_ascii=False)
        if "磁盘" in last_user or "进程" in last_user:
            # 恢复上一类别（磁盘/进程），但当前输入含新关键词优先
            if "进程" in last_user or "process" in last_user.lower():
                return json.dumps({
                    "intent": "process_analysis",
                    "plan": [{"tool": "process_list", "arguments": {"limit": 10}, "reason": "追问：查看进程"}],
                }, ensure_ascii=False)
            return json.dumps({
                "intent": "disk_analysis",
                "plan": [{"tool": "disk_usage", "arguments": {"path": "."}, "reason": "追问：分析磁盘"}],
            }, ensure_ascii=False)

    # 知识库检索
    if any(k in user_input for k in ("知识库", "知识", "FAQ", "faq")):
        query = user_input or "运维知识"
        return json.dumps({
            "intent": "knowledge_query",
            "plan": [{"tool": "rag_search", "arguments": {"query": query}, "reason": "从知识库检索相关知识"}],
        }, ensure_ascii=False)

    if "磁盘" in user_input or "disk" in lowered:
        return json.dumps({
            "intent": "disk_analysis",
            "plan": [{"tool": "disk_usage", "arguments": {"path": "."}, "reason": "分析磁盘使用率"}],
        }, ensure_ascii=False)
    if "进程" in user_input or "process" in lowered:
        return json.dumps({
            "intent": "process_analysis",
            "plan": [{"tool": "process_list", "arguments": {"limit": 10}, "reason": "查看进程列表"}],
        }, ensure_ascii=False)
    if "重启" in user_input or "restart" in lowered:
        return json.dumps({
            "intent": "service_restart",
            "plan": [{"tool": "service_restart", "arguments": {"service": "sshd"}, "reason": "重启服务需要审批"}],
        }, ensure_ascii=False)

    # 空计划（"你好"等通用咨询不走工具）
    return json.dumps({"intent": "general_help", "plan": []}, ensure_ascii=False)


def _fake_route(messages: list[BaseMessage]) -> str:
    """场景路由的确定性分类（scripted model for testing）。

    支持追问检测：当当前输入含指代词时，参考上一轮的场景分类。
    """
    from langchain_core.messages import HumanMessage

    # 提取当前用户输入和上一轮用户输入
    user_input = ""
    prev_user_msg = ""
    for m in messages:
        if isinstance(m, HumanMessage):
            content = m.content
            if content.startswith("当前用户输入："):
                user_input = content[len("当前用户输入："):]
            elif content.startswith("上一轮用户输入："):
                prev_user_msg = content[len("上一轮用户输入："):]

    lowered = user_input.lower()

    # 追问检测：含指代词 → 参考上一轮分类
    follow_up_markers = ("那", "那 ", "也", "还", "另外", "再")
    is_follow_up = any(m in user_input for m in follow_up_markers)
    if is_follow_up and prev_user_msg:
        # 根据上一轮输入推断场景
        prev_lower = prev_user_msg.lower()
        if "端口" in prev_user_msg or "port" in prev_lower:
            return json.dumps({
                "route": "readonly_diagnosis",
                "reason": f"追问：基于上一轮端口查询",
            }, ensure_ascii=False)
        if "磁盘" in prev_user_msg or "disk" in prev_lower or \
           "进程" in prev_user_msg or "process" in prev_lower:
            return json.dumps({
                "route": "readonly_diagnosis",
                "reason": f"追问：基于上一轮只读诊断",
            }, ensure_ascii=False)

    # mutation: 副作用操作
    mutation_kws = ("重启", "restart", "修改配置", "关机", "shutdown", "reboot", "格式化")
    if any(kw in user_input or kw in lowered for kw in mutation_kws):
        return json.dumps({
            "route": "mutation",
            "reason": "检测到副作用操作意图",
        }, ensure_ascii=False)

    # knowledge: 知识查询
    knowledge_kws = ("知识库", "知识", "faq", "如何", "怎么", "怎样", "什么是", "原理")
    if any(kw in user_input.lower() for kw in knowledge_kws):
        return json.dumps({
            "route": "knowledge",
            "reason": "检测到知识查询意图",
        }, ensure_ascii=False)

    # readonly_diagnosis: 只读诊断
    readonly_kws = ("磁盘", "disk", "进程", "process", "端口", "port", "日志", "log",
                    "服务状态", "service", "目录", "directory", "分析", "查看")
    if any(kw in user_input or kw in lowered for kw in readonly_kws):
        return json.dumps({
            "route": "readonly_diagnosis",
            "reason": "检测到只读诊断意图",
        }, ensure_ascii=False)

    # consult: 默认
    return json.dumps({
        "route": "consult",
        "reason": "普通咨询",
    }, ensure_ascii=False)


def _fake_react_decide(messages: list[BaseMessage], last: str) -> str:
    """ReAct 决策的确定性行为（scripted model for testing）。

    策略：
      - 第 1 轮（无 Observation）：根据用户问题选择第一个只读工具
      - 第 2 轮（有 1 个 Observation）：选择另一个只读工具（证明真实循环）
      - 第 3 轮+：返回 final answer

    这样测试可以验证：Observation → 模型再次决策 → 不同工具 → final。
    """
    from langchain_core.messages import HumanMessage

    # 提取用户问题（从 "用户问题：..." 中）
    user_input = ""
    for m in messages:
        if isinstance(m, HumanMessage):
            content = m.content
            if content.startswith("用户问题："):
                user_input = content[len("用户问题："):]
                break

    # 统计已有 Observation 数量（通过 ToolMessage 判断 — Observation 以 ToolMessage 回传）
    # 注意：readonly_decide_node 把工具输出建模为 ToolMessage（不可信数据，
    # 与 HumanMessage 用户指令结构化隔离），所以这里统计 ToolMessage 数量。
    obs_count = _count_tool_messages(messages)

    lowered = user_input.lower()

    # 追问检测：含指代词且含新参数 → 恢复上一工具类别，替换参数
    follow_up_markers = ("那", "那 ", "也", "还", "另外", "再")
    is_follow_up = any(m in user_input for m in follow_up_markers)

    # 从消息历史中提取上一轮工具类别（用于追问恢复）
    prev_tool_category = ""
    if is_follow_up:
        for m in messages:
            if isinstance(m, HumanMessage):
                content = m.content
                if "端口" in content or "port" in content.lower():
                    prev_tool_category = "port"
                    break
                if "磁盘" in content or "disk" in content.lower():
                    prev_tool_category = "disk"
                    break
                if "进程" in content or "process" in content.lower():
                    prev_tool_category = "process"
                    break

    # 第 3 轮+：返回 final answer
    if obs_count >= 2:
        return json.dumps({
            "action": "final",
            "answer": f"根据 {obs_count} 个工具观测结果：系统运行正常，已完成只读诊断。建议持续监控关键指标。",
        }, ensure_ascii=False)

    # 追问恢复：根据上一工具类别 + 新参数
    if is_follow_up and prev_tool_category == "port":
        port = _port_from_text(user_input) or 8080
        return json.dumps({
            "action": "tool",
            "tool": "port_lookup",
            "arguments": {"port": port},
        }, ensure_ascii=False)

    # 第 1 轮：选择第一个只读工具
    if obs_count == 0:
        if "磁盘" in user_input or "disk" in lowered:
            return json.dumps({
                "action": "tool",
                "tool": "disk_usage",
                "arguments": {"path": "."},
            }, ensure_ascii=False)
        if "进程" in user_input or "process" in lowered:
            return json.dumps({
                "action": "tool",
                "tool": "process_list",
                "arguments": {"limit": 10},
            }, ensure_ascii=False)
        if "端口" in user_input or "port" in lowered:
            port = _port_from_text(user_input) or 8080
            return json.dumps({
                "action": "tool",
                "tool": "port_lookup",
                "arguments": {"port": port},
            }, ensure_ascii=False)
        # 默认第一个工具
        return json.dumps({
            "action": "tool",
            "tool": "disk_usage",
            "arguments": {"path": "."},
        }, ensure_ascii=False)

    # 第 2 轮：选择另一个只读工具（证明真实循环）
    if obs_count == 1:
        # 根据第一轮的工具选择不同的第二个工具
        if "磁盘" in user_input or "disk" in lowered:
            return json.dumps({
                "action": "tool",
                "tool": "process_list",
                "arguments": {"limit": 10},
            }, ensure_ascii=False)
        return json.dumps({
            "action": "tool",
            "tool": "disk_usage",
            "arguments": {"path": "."},
        }, ensure_ascii=False)

    # 兜底
    return json.dumps({
        "action": "final",
        "answer": "根据工具观测结果：系统正常。",
    }, ensure_ascii=False)
