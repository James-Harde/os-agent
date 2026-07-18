"""LangChain ChatModel 封装。

教学要点：
  LangChain 抽象了一个统一接口 BaseChatModel，所有模型提供商（OpenAI、
  Anthropic、DeepSeek、本地模型）都实现这个接口。

  你写代码时面对的是 BaseChatModel，不关心后面接的是哪个模型。
  换模型只改一行初始化代码，业务逻辑零改动。

  对比旧版：
    旧版手写 urllib 拼请求，直接发 HTTP 给 DeepSeek。
    现在用 langchain_openai.ChatOpenAI，它兼容 OpenAI 协议，接入 DeepSeek
    只需要改 base_url。以后换 Claude 只需要换成 ChatAnthropic。
"""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI


def build_chat_model() -> BaseChatModel:
    """根据 .env 配置构建一个 ChatModel 实例。

    环境变量（沿用旧版的命名）：
      OPENAI_COMPATIBLE_BASE_URL  — 模型 API 地址
      OPENAI_COMPATIBLE_API_KEY   — API Key
      OPENAI_COMPATIBLE_MODEL     — 模型名称
      OPENAI_COMPATIBLE_TIMEOUT   — 超时秒数

    Returns:
        绑好配置的 ChatOpenAI 实例（类型标注为 BaseChatModel，
        意味着你的节点代码不依赖具体的 ChatOpenAI）。
    """
    return ChatOpenAI(
        base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL", "").rstrip("/"),
        api_key=os.getenv("OPENAI_COMPATIBLE_API_KEY", ""),
        model=os.getenv("OPENAI_COMPATIBLE_MODEL", ""),
        timeout=float(os.getenv("OPENAI_COMPATIBLE_TIMEOUT", "20")),
        temperature=0.1,
    )


# 单例（整个应用共享一个 model 实例）
_chat_model: BaseChatModel | None = None


def get_chat_model() -> BaseChatModel:
    """获取全局单例 ChatModel。"""
    global _chat_model
    if _chat_model is None:
        _chat_model = build_chat_model()
    return _chat_model
