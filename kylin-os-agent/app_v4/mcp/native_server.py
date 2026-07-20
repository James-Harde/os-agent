"""标准 MCP Server — 基于官方 MCP Python SDK。

修复 audit #11：不再维护"长得像 MCP"的字典分发器，改用官方 SDK 的
FastMCP + stdio transport，支持标准 initialize 生命周期。

和 /api/chat 使用同一套工具、同一安全策略、同一审计链路。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from mcp.server import FastMCP
from mcp.types import TextContent, Tool

from app_v4.tools.registry import TOOL_BY_NAME, get_tool_permission, get_tools
from app_v4.safety.guard import SafetyGuard
from app_v4.audit.logger import get_audit_logger

logger = logging.getLogger("app_v4.mcp.native")

# 创建 FastMCP 服务器实例（标准 initialize 生命周期）
mcp = FastMCP("kylin-secure-os-agent")


def _tool_input_schema(tool_obj) -> dict[str, Any]:
    """从 LangChain @tool 对象提取 JSON Schema。"""
    if hasattr(tool_obj, "args_schema") and tool_obj.args_schema:
        try:
            return tool_obj.args_schema.model_json_schema()
        except Exception:
            return {}
    if hasattr(tool_obj, "args") and tool_obj.args:
        return dict(tool_obj.args)
    return {}


def _sanitize_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """脱敏参数（移除可能的敏感路径细节，保留结构）。"""
    return {k: v for k, v in arguments.items()}


# ---------------------------------------------------------------------------
# 注册工具：遍历所有 auto 权限工具，包装为 MCP Tool
# ---------------------------------------------------------------------------
def register_tools():
    """把所有 auto 权限工具注册到 FastMCP。"""
    guard = SafetyGuard()
    for tool in get_tools():
        permission = get_tool_permission(tool.name)
        if permission != "auto":
            continue  # confirm/deny 工具不暴露给 MCP 自动调用

        tool_obj = TOOL_BY_NAME.get(tool.name)
        if tool_obj is None:
            continue

        schema = _tool_input_schema(tool_obj)
        description = tool.description or f"工具: {tool.name}"

        # 使用闭包绑定当前工具，避免 late-binding 问题
        def make_handler(tn: str, tobj):
            async def handler(**kwargs):
                # 1. 参数安全扫描
                args_text = json.dumps(kwargs, ensure_ascii=False)
                scan = guard.scan_untrusted_output(
                    {"tool_name": tn, "result": {"args": args_text}})
                if scan["detected"]:
                    return [TextContent(
                        type="text",
                        text=json.dumps({
                            "status": "blocked",
                            "error": f"注入检测: {scan['reasons']}",
                        }, ensure_ascii=False),
                    )]

                # 2. 执行
                t0 = time.monotonic()
                try:
                    result = tobj.invoke(kwargs)
                    result["_mcp_duration_ms"] = round((time.monotonic() - t0) * 1000, 2)
                    status = "success"
                except Exception as exc:
                    result = {"status": "error", "error": str(exc)}
                    status = "error"

                # 3. 写入审计（MCP 调用走统一审计，修复 audit #11）
                get_audit_logger().record(
                    conversation_id="mcp",
                    result={
                        "run_id": "",
                        "thread_id": "mcp",
                        "intent": f"mcp_call:{tn}",
                        "guard_decision": "allow",
                        "guard_reasons": [],
                        "tool_calls": [{"tool_name": tn, "status": status,
                                       "data": result, "arguments": _sanitize_args(kwargs)}],
                        "answer": result.get("message", ""),
                        "answer_source": "mcp_tool",
                        "trace_steps": [],
                    },
                )

                return [TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False),
                )]
            return handler

        mcp.add_tool(
            fn=make_handler(tool.name, tool_obj),
            name=tool.name,
            description=description,
            structured_output=False,
        )


# 初始化时注册工具
register_tools()


# ---------------------------------------------------------------------------
# 运行入口
# ---------------------------------------------------------------------------
async def run_stdio():
    """通过 stdio transport 运行 MCP Server（标准传输）。"""
    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(run_stdio())
