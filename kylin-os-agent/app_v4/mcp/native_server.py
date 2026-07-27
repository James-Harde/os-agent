"""标准 MCP Server — 基于官方 MCP Python SDK (FastMCP)。

修复 audit #11/#17/#18：
  - 不再用 exec() 动态生成 handler（改用闭包工厂 + 显式 signature）。
  - 注册全部工具（auto + confirm），含风险/权限元数据。
  - 复用统一 ToolApplicationService（§4.4 #2），不复制执行/安全逻辑。
  - schema 保留类型、required、default、范围。

和 /api/chat 使用同一套工具、同一安全策略、同一审计链路。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import time
from typing import Any

from mcp.server import FastMCP
from mcp.types import TextContent, Tool

from app_v4.tools.registry import TOOL_BY_NAME, get_tool_permission, get_tools
from app_v4.safety.guard import SafetyGuard

logger = logging.getLogger("app_v4.mcp.native")

# 创建 FastMCP 服务器实例（标准 initialize 生命周期）
mcp = FastMCP("kylin-secure-os-agent")


def _json_schema_type_to_python(json_type: str) -> type:
    """JSON Schema 类型 → Python 类型（用于 handler 签名 annotation）。"""
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(json_type, Any)


def _tool_input_schema(tool_obj) -> dict[str, Any]:
    """从 LangChain @tool 对象提取 JSON Schema（确定性，不用 exec）。"""
    if hasattr(tool_obj, "args_schema") and tool_obj.args_schema:
        try:
            return tool_obj.args_schema.model_json_schema()
        except Exception:
            return {}
    return {}


def _make_handler(
    tool_name: str,
    tool_obj,
    input_schema: dict,
    app_service,
):
    """为指定工具生成带显式参数的 async handler（不用 exec）。

    通过闭包捕获 tool_name/tool_obj，并构造 inspect.Signature
    使 FastMCP 能正确解析参数名、类型、required、default。
    """
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    # 构造显式参数列表
    params = []
    annotations = {}
    for pname, pschema in properties.items():
        ptype = _json_schema_type_to_python(pschema.get("type", "string"))
        if pname in required:
            param = inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ptype
            )
        else:
            default = pschema.get("default", None)
            param = inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default, annotation=ptype,
            )
        params.append(param)
        annotations[pname] = ptype

    sig = inspect.Signature(params)

    async def handler(**kwargs):
        guard = SafetyGuard()
        # 1. 参数安全扫描
        scan = guard.scan_untrusted_output(
            {"tool_name": tool_name, "result": {"args": json.dumps(kwargs, ensure_ascii=False)}})
        if scan["detected"]:
            return [TextContent(
                type="text",
                text=json.dumps(
                    {"status": "blocked", "error": f"注入检测: {scan['reasons']}"},
                    ensure_ascii=False),
            )]

        # 2. 执行（可变更工具经 ToolApplicationService，校验审批凭证）
        t0 = time.monotonic()
        permission = get_tool_permission(tool_name)

        if permission == "confirm":
            # confirm 工具：经 app_service 校验审批凭证后执行
            approval_id = kwargs.pop("_approval_id", None)
            approval_status = kwargs.pop("_approval_status", None)
            result = app_service.execute_mutation(
                tool_name=tool_name,
                arguments=kwargs,
                approval_id=approval_id,
                approval_status=approval_status,
            )
        else:
            try:
                result = tool_obj.invoke(kwargs)
                status = "success"
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
                status = "error"
            result["_mcp_duration_ms"] = round((time.monotonic() - t0) * 1000, 2)
            scan_out = app_service.scan_output(tool_name, result)
            result["_output_scan"] = {
                "detected": scan_out["detected"],
                "risk_level": scan_out["risk_level"],
            }

        # 3. 写入审计（非空 run_id、完整 Trace，修复 audit #11）
        run_id = f"mcp:{tool_name}:{hashlib.sha256(json.dumps(kwargs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]}"
        from app_v4.audit.logger import get_audit_logger
        get_audit_logger().record(
            conversation_id="mcp",
            result={
                "run_id": run_id,
                "thread_id": "mcp",
                "intent": f"mcp_call:{tool_name}",
                "guard_decision": "allow",
                "guard_reasons": [],
                "tool_calls": [{"tool_name": tool_name, "status": result.get("status", "success"),
                               "data": result, "arguments": kwargs}],
                "answer": result.get("message", ""),
                "answer_source": "mcp_tool",
                "trace_steps": [{"node": "mcp_call", "duration_ms": result.get("_mcp_duration_ms", 0),
                                "detail": {"tool_name": tool_name}}],
            },
        )

        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    # 挂载显式 signature 供 FastMCP 内省
    handler.__signature__ = sig
    handler.__annotations__ = annotations
    # 用工具名作函数名，使 FastMCP 生成的 title 干净（如 "process_list"）
    handler.__name__ = tool_name
    # 保证函数名唯一（避免同名覆盖）
    handler.__qualname__ = f"{tool_name}_mcp_handler"
    return handler


def _risk_level_for(tool_name: str) -> str:
    """根据工具权限返回风险等级元数据。"""
    perm = get_tool_permission(tool_name)
    if perm == "confirm":
        return "medium"
    return "low"


def register_tools(app_service=None):
    """把所有工具（auto + confirm）注册到 FastMCP，含风险/权限元数据。"""
    if app_service is None:
        from app_v4.tools.application import ToolApplicationService
        app_service = ToolApplicationService()

    for tool in get_tools():
        tool_obj = TOOL_BY_NAME.get(tool.name)
        if tool_obj is None:
            continue

        schema = _tool_input_schema(tool_obj)
        handler = _make_handler(tool.name, tool_obj, schema, app_service)
        description = tool.description or f"工具: {tool.name}"
        permission = get_tool_permission(tool.name)
        risk = _risk_level_for(tool.name)

        # 在 description 附加权限/风险元数据（便于客户端渐进披露）
        meta_suffix = f" [权限:{permission}|风险:{risk}]"
        full_description = description + meta_suffix

        mcp.add_tool(
            fn=handler,
            name=tool.name,
            description=full_description,
            structured_output=False,
        )


# 初始化时注册工具（可用 set_app_service 注入共享实例）
_registered = False


def set_app_service(app_service):
    """注入共享的 ToolApplicationService（与 Graph 共用同一实例）。"""
    global _registered
    # 清空已有工具，重新注册（确保共享 app_service）
    if hasattr(mcp, "_tool_manager"):
        mcp._tool_manager._tools.clear()
    register_tools(app_service)
    _registered = True


# 默认注册（使用独立 ToolApplicationService，可被 set_app_service 覆盖）
if not _registered:
    register_tools()
    _registered = True


# ---------------------------------------------------------------------------
# 运行入口
# ---------------------------------------------------------------------------
async def run_stdio():
    """通过 stdio transport 运行 MCP Server（标准传输）。"""
    await mcp.run_stdio_async()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_stdio())
