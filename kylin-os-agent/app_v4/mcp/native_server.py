"""标准 MCP Server — 基于官方 MCP Python SDK (FastMCP) + Streamable HTTP。

生产唯一 MCP Server 路径：
  - 通过 :func:`create_mcp_server` 工厂构建可注入的 FastMCP 实例，
    禁止修改私有 ``_tool_manager``。
  - **最小权限**：仅注册 ``auto`` 只读工具；confirm/mutation 保留在
    LangGraph policy → HITL → 服务端审批链，不暴露给外部 MCP 客户端。
  - 复用统一 :class:`ToolApplicationService`（§4.4 #2），不直接 tool_obj.invoke()。
  - tools/list 返回正确 inputSchema、ToolAnnotations 和结构化 meta
    （permission、risk_level），风险不拼进 description。
  - 每次调用生成唯一 invocation ID（UUID），相同工具+参数也不共用 Trace ID。
  - 成功/失败/拒绝/注入阻断都走同一错误映射、输出扫描和审计边界。
  - 已知工具校验失败、策略阻断、执行失败符合 MCP ``isError`` 语义。
  - MCP 审计和 Agent 使用同一可注入 :class:`AuditLogger`。

和 ``/api/chat`` 使用同一套工具、同一安全策略、同一审计链路。

启动命令（Streamable HTTP，生产默认传输）::

    .venv\\Scripts\\python -m app_v4.mcp.native_server
    # 默认监听 127.0.0.1:8001，MCP 端点路径 /mcp
    # 可通过环境变量覆盖：MCP_HOST / MCP_PORT
"""

from __future__ import annotations

import inspect
import json
import logging
import time
import uuid
from typing import Any

from mcp.server import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from app_v4.tools.registry import TOOL_BY_NAME, get_tool_permission, get_tools

logger = logging.getLogger("app_v4.mcp.native")

_ERROR_STATUSES = frozenset(
    {"error", "blocked", "unavailable", "timeout", "disabled", "denied", "rejected"}
)
_POLICY_BLOCK_STATUSES = frozenset({"blocked", "denied", "rejected"})


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


def _risk_level_for(tool_name: str) -> str:
    """根据工具权限返回风险等级元数据。"""
    perm = get_tool_permission(tool_name)
    if perm == "confirm":
        return "medium"
    if perm == "deny":
        return "high"
    return "low"


def _make_handler(
    tool_name: str,
    tool_obj,
    input_schema: dict,
    app_service,
    audit_logger,
):
    """为指定工具生成带显式参数的 async handler（不用 exec）。

    通过闭包捕获 tool_name/tool_obj/app_service/audit_logger，并构造
    inspect.Signature 使 FastMCP 能正确解析参数名、类型、required、default。

    执行边界（finding #4/#5）：
      - 注入阻断：先写审计，再返回 isError（执行次数为 0）。
      - 执行：统一经 app_service.execute_auto()，不直接 tool_obj.invoke()。
      - 所有结果（成功/失败/拒绝/注入阻断）都写审计。
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
        # 每次调用生成唯一 invocation ID（finding #6）
        invocation_id = f"mcp:{tool_name}:{uuid.uuid4().hex}"

        # 1. 输入注入扫描（finding #4：阻断前先写审计）
        from app_v4.safety.guard import SafetyGuard
        guard = SafetyGuard()
        scan = guard.scan_untrusted_output(
            {"tool_name": tool_name, "result": {"args": json.dumps(kwargs, ensure_ascii=False)}})
        if scan["detected"]:
            blocked = {
                "status": "blocked",
                "error": f"注入检测: {scan['reasons']}",
                "source": "mcp_input_scan",
                "tool_name": tool_name,
                "invocation_id": invocation_id,
            }
            _write_audit(audit_logger, tool_name, invocation_id, kwargs, blocked)
            # finding #3：阻断返回 isError=True（执行次数为 0）
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=json.dumps(blocked, ensure_ascii=False))],
            )

        # 2. 执行 — 统一经 ToolApplicationService（finding #5）
        t0 = time.monotonic()
        result = app_service.execute_auto(tool_name, kwargs)
        result["_mcp_duration_ms"] = round((time.monotonic() - t0) * 1000, 2)
        result["invocation_id"] = invocation_id

        # 3. 写审计（所有结果都写，finding #4）
        _write_audit(audit_logger, tool_name, invocation_id, kwargs, result)

        # 4. 错误映射 → isError（finding #3）
        if result.get("status") in _ERROR_STATUSES:
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
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


def _write_audit(audit_logger, tool_name, invocation_id, arguments, result) -> None:
    """统一审计写入（MCP 和 Agent 共享同一 AuditLogger）。"""
    status = result.get("status", "success")
    policy_blocked = status in _POLICY_BLOCK_STATUSES
    audit_logger.record(
        conversation_id="mcp",
        result={
            "run_id": invocation_id,
            "thread_id": "mcp",
            "intent": f"mcp_call:{tool_name}",
            "guard_decision": "block" if policy_blocked else "allow",
            "guard_reasons": [result["error"]] if policy_blocked and result.get("error") else [],
            "tool_calls": [{"tool_name": tool_name, "status": status,
                           "data": result, "arguments": arguments}],
            "answer": result.get("message", ""),
            "answer_source": "mcp_tool",
            "trace_steps": [{"node": "mcp_call", "duration_ms": result.get("_mcp_duration_ms", 0),
                            "detail": {"tool_name": tool_name, "invocation_id": invocation_id}}],
        },
    )


def create_mcp_server(
    app_service=None,
    audit_logger=None,
    *,
    host: str = "127.0.0.1",
    port: int = 8001,
) -> FastMCP:
    """构建一个注册好 auto 只读工具的 FastMCP 实例。

    工厂模式：每次调用返回全新实例，避免模块级单例的 session manager
    只能 run 一次的局限。测试与 E2E 可各自创建独立实例并绑定不同端口。

    Args:
        app_service: 统一工具应用服务；None 则创建默认实例。
        audit_logger: 可注入审计日志器；None 时使用与 Web Agent 相同的
            默认 SQLite 路径。独立进程通过同一存储共享审计，而不依赖
            Web Agent 的 MCP Client 容器。
        host: 监听地址。
        port: 监听端口。
    """
    if app_service is None:
        from app_v4.tools.application import ToolApplicationService
        app_service = ToolApplicationService()

    if audit_logger is None:
        from app_v4.audit.logger import AuditLogger
        audit_logger = AuditLogger()

    server = FastMCP("kylin-secure-os-agent", host=host, port=port)

    for tool in get_tools():
        # 最小权限（finding #7）：外部 MCP 仅暴露 auto 只读工具
        permission = get_tool_permission(tool.name)
        if permission != "auto":
            continue

        tool_obj = TOOL_BY_NAME.get(tool.name)
        if tool_obj is None:
            continue

        schema = _tool_input_schema(tool_obj)
        handler = _make_handler(tool.name, tool_obj, schema, app_service, audit_logger)
        risk = _risk_level_for(tool.name)

        # finding #2：结构化 annotations + meta（风险不拼进 description）
        annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
        meta = {
            "permission": permission,
            "risk_level": risk,
        }

        server.add_tool(
            fn=handler,
            name=tool.name,
            description=tool.description or f"工具: {tool.name}",
            annotations=annotations,
            meta=meta,
            structured_output=False,
        )

    return server


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
