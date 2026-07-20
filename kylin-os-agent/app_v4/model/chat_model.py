"""LangChain ChatModel 封装。

提供 fake model 用于测试（不依赖真实 LLM），通过环境变量切换：
  APP_V4_USE_FAKE_MODEL=true  → 使用确定性假模型
  默认 → 使用真实 ChatOpenAI
"""

from __future__ import annotations

import os
import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI


def build_chat_model() -> BaseChatModel:
    """构建 ChatModel。如果设置了 USE_FAKE_MODEL 则返回假模型。"""
    if os.getenv("APP_V4_USE_FAKE_MODEL", "").lower() in ("1", "true", "yes"):
        return _FakeChatModel()
    return ChatOpenAI(
        base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL", "").rstrip("/"),
        api_key=os.getenv("OPENAI_COMPATIBLE_API_KEY", ""),
        model=os.getenv("OPENAI_COMPATIBLE_MODEL", ""),
        timeout=float(os.getenv("OPENAI_COMPATIBLE_TIMEOUT", "20")),
        temperature=0.1,
    )


_chat_model: BaseChatModel | None = None


def get_chat_model() -> BaseChatModel:
    """全局单例。"""
    global _chat_model
    if _chat_model is None:
        _chat_model = build_chat_model()
    return _chat_model


class _FakeChatModel(BaseChatModel):
    """确定性假模型，用于测试。不调用任何外部 API。

    行为：
      - 如果消息里包含 "plan" 相关提示 → 返回一个固定 plan（调 disk_usage）
      - 否则 → 返回一个固定总结
    """

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> Any:
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatResult, ChatGeneration

        # 简单启发式：看最后一条消息内容
        last = messages[-1].content if messages else ""
        content = _fake_response(last)

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"


def _extract_user_input(prompt: str) -> str:
    """从规划请求 JSON 中提取 user_input 字段（容错：失败返回空串）。

    复用 graph.nodes._extract_json 解析 JSON（正确处理转义引号），
    避免自行实现 fallback 正则导致转义字符截断。
    """
    from app_v4.graph.nodes import _extract_json

    data = _extract_json(prompt)
    if isinstance(data, dict):
        return data.get("user_input", "")
    return ""


def _fake_response(prompt: str) -> str:
    """根据 prompt 内容返回确定性响应。

    关键设计：意图判断只基于 user_input（从 prompt JSON 中提取），
    而不是整个 prompt（含 allowed_tools / 工具描述），避免"你好"误判为磁盘。
    """
    # 规划请求：返回 JSON plan
    if "allowed_tools" in prompt or "plan" in prompt.lower():
        # 只从 user_input 提取意图，不扫描整个 prompt
        user_input = _extract_user_input(prompt)
        lowered = user_input.lower()

        # 知识库检索意图
        if any(k in user_input for k in ("知识库", "知识", "FAQ", "faq")):
            query = user_input or "运维知识"
            return json.dumps({
                "intent": "knowledge_query",
                "plan": [{"tool": "rag_search", "arguments": {"query": query}, "reason": "从知识库检索相关知识"}],
            }, ensure_ascii=False)
        # 判断用户意图（仅匹配 user_input）
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
        if "端口" in user_input or "port" in lowered:
            return json.dumps({
                "intent": "port_analysis",
                "plan": [{"tool": "port_lookup", "arguments": {"port": 8080}, "reason": "查询端口占用"}],
            }, ensure_ascii=False)
        # 重启服务 → confirm 工具（service_restart 不在 allowed_tools 里，走默认空计划）
        if "重启" in user_input or "restart" in lowered:
            return json.dumps({
                "intent": "service_restart",
                "plan": [{"tool": "service_restart", "arguments": {"service": "sshd"}, "reason": "重启服务需要审批"}],
            }, ensure_ascii=False)
        # 默认：空计划（"你好"等通用咨询不走工具）
        return json.dumps({"intent": "general_help", "plan": []}, ensure_ascii=False)

    # 总结请求
    return "根据工具结果：系统运行正常，未发现异常。建议：持续监控关键指标。"
