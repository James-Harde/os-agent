"""工具注册 — LangChain 版。

教学要点：
  旧版手写 ToolSpec + ToolRegistry 类来管理工具生命周期。
  LangChain 没有"注册表"这个概念——工具就是带 @tool 的函数，
  用一个 list 收集起来 bind 给 LLM 就行。

  真正的"调度"（哪个工具能跑、哪个要审批）不再在这里处理，
  而是交给 LangGraph 的条件边（edges.py）来决定。
"""

from app_v2.tools.system_tools import (
    disk_usage,
    directory_usage,
    port_lookup,
    process_list,
    system_logs,
    service_status,
    prompt_injection_scan,
)

# 所有"安全可自动执行"的工具列表
# LLM 从这个列表里选工具，框架负责解析 LLM 输出并调用对应函数
SAFE_AUTO_TOOLS = [
    disk_usage,
    directory_usage,
    port_lookup,
    process_list,
    system_logs,
    service_status,
    prompt_injection_scan,
]

# 工具名 → 工具对象 的快速查找表
TOOL_BY_NAME = {t.name: t for t in SAFE_AUTO_TOOLS}


def get_tools() -> list:
    """返回绑定给 LLM 的工具列表。"""
    return SAFE_AUTO_TOOLS


def get_tool_names() -> set[str]:
    """返回所有安全工具的名称集合。"""
    return set(TOOL_BY_NAME.keys())
