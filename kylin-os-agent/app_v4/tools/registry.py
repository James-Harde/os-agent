"""工具注册 — 收集所有 @tool 函数 + 权限声明。

P1 新增：每个工具声明执行权限等级
  - auto:   只读低风险，自动执行（默认）
  - confirm: 有副作用，需要人工审批（暂停）
  - deny:   禁止执行（如 file_delete）
"""

from app_v4.tools.system_tools import (
    disk_usage,
    directory_usage,
    port_lookup,
    process_list,
    system_logs,
    service_status,
    prompt_injection_scan,
    service_restart,
    rag_search,
)

# 工具执行权限声明
TOOL_PERMISSIONS: dict[str, str] = {
    "disk_usage": "auto",
    "directory_usage": "auto",
    "port_lookup": "auto",
    "process_list": "auto",
    "system_logs": "auto",
    "service_status": "auto",
    "prompt_injection_scan": "auto",
    "rag_search": "auto",            # RAG 知识库检索（只读）
    "service_restart": "confirm",   # 有副作用：重启服务
}

# 安全可自动执行的工具列表（auto 权限）
SAFE_AUTO_TOOLS = [
    disk_usage, directory_usage, port_lookup,
    process_list, system_logs, service_status, prompt_injection_scan,
    rag_search,
]

# confirm 类工具（需要审批）
CONFIRM_TOOLS = [service_restart]

# 工具名 → 工具对象
TOOL_BY_NAME = {t.name: t for t in SAFE_AUTO_TOOLS + CONFIRM_TOOLS}


def get_tools() -> list:
    return SAFE_AUTO_TOOLS + CONFIRM_TOOLS


def get_tool_names() -> set[str]:
    return set(TOOL_BY_NAME.keys())


def get_auto_tool_names() -> set[str]:
    """返回 auto 权限的工具名（plan_node 只暴露这些给 LLM）。"""
    return {name for name, perm in TOOL_PERMISSIONS.items() if perm == "auto"}


def get_tool_permission(tool_name: str) -> str:
    """获取工具权限等级。"""
    return TOOL_PERMISSIONS.get(tool_name, "deny")


def get_tool_description(tool_name: str) -> str:
    """获取工具描述（用于 LLM prompt 与渐进披露排序）。"""
    tool = TOOL_BY_NAME.get(tool_name)
    if tool is None:
        return ""
    return getattr(tool, "description", "") or ""


def get_tool_descriptions() -> dict[str, str]:
    """返回 {tool_name: description} 全量映射。"""
    return {name: get_tool_description(name) for name in TOOL_BY_NAME}
