"""Kylin Secure OS Agent v3 — 纯 LangChain 版.

教学要点：
  对比 app_v2（LangGraph）的核心差异：
    - LangGraph 的循环是"你画的图"，节点和边显式可见
    - LangChain 的循环是 AgentExecutor 内部的黑盒，你不写循环，框架替你跑
    - 两者都用 @tool、ChatModel —— 工具层和模型层完全一样
    - 真正差异在于"谁来控制 agent 的运行逻辑"
"""
