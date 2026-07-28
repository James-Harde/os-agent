"""标准 MCP Server — 基于官方 MCP Python SDK (FastMCP) + Streamable HTTP。

生产唯一 MCP Server 路径：
  - 通过 :func:`create_mcp_server` 工厂构建可注入的 FastMCP 实例，
    禁止修改私有 ``_tool_manager``。
  - 注册全部工具（auto + confirm），含风险/权限元数据。
  - 复用统一 :class:`ToolApplicationService`（§4.4 #2），不复制执行/安全逻辑。
  - schema 保留类型、required、default、范围。

和 ``/api/chat`` 使用同一套工具、同一安全策略、同一审计链路。

启动命令（Streamable HTTP，生产默认传输）::

    .venv\\Scripts\\python -m app_v4.mcp.native_server
    # 默认监听 127.0.0.1:8001，MCP 端点路径 /mcp
    # 可通过环境变量覆盖：MCP_HOST / MCP_PORT
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

    通过闭包捕获 tool_name/tool_obj/app_service，并构造 inspect.Signature
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


def create_mcp_server(
    app_service=None,
    *,
    host: str = "127.0.0.1",
    port: int = 8001,
) -> FastMCP:
    """构建一个注册好全部工具的 FastMCP 实例（可注入共享 ToolApplicationService）。

    工厂模式：每次调用返回全新实例，避免模块级单例的 session manager
    只能 run 一次的局限。测试与 E2E 可各自创建独立实例并绑定不同端口。
    """
    if app_service is None:
        from app_v4.tools.application import ToolApplicationService
        app_service = ToolApplicationService()

    server = FastMCP("kylin-secure-os-agent", host=host, port=port)

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

        server.add_tool(
            fn=handler,
            name=tool.name,
            description=full_description,
            structured_output=False,
        )

    return server


# ---------------------------------------------------------------------------
# 默认单例（向后兼容：旧测试引用 native_server.mcp 仍可用）
# ---------------------------------------------------------------------------
mcp: FastMCP = create_mcp_server()


# ---------------------------------------------------------------------------
# 运行入口
# ---------------------------------------------------------------------------
async def run_streamable_http(host: str = "127.0.0.1", port: int = 8001):
    """通过 Streamable HTTP transport 运行 MCP Server（生产默认传输）。"""
    server = create_mcp_server(host=host, port=port)
    await server.run_streamable_http_async()


if __name__ == "__main__":
    import asyncio
    import os
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8001"))
    asyncio.run(run_streamable_http(host=host, port=port))
