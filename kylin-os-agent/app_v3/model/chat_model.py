"""LangChain ChatModel — 和 v2 完全一样。

教学要点：
  ChatModel 抽象是 LangChain 的，LangGraph 底层也用它。
  所以模型层在两个版本之间共享。
"""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

_chat_model: BaseChatModel | None = None


def get_chat_model() -> BaseChatModel:
    global _chat_model
    if _chat_model is None:
        _chat_model = ChatOpenAI(
            base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("OPENAI_COMPATIBLE_API_KEY", ""),
            model=os.getenv("OPENAI_COMPATIBLE_MODEL", ""),
            timeout=float(os.getenv("OPENAI_COMPATIBLE_TIMEOUT", "20")),
            temperature=0.1,
        )
    return _chat_model
