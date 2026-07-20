"""MCP Server 实现 — JSON-RPC  protocol。

暴露：
  - tools/list：返回工具列表（名/描述/schema/风险/权限）
  - tools/call：调用工具（复用同一安全策略 + 审计链路）

和 /api/chat 使用同一套工具、同一安全策略、同一审计日志。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app_v4.mcp.schemas import (
    MCPToolInfo, MCPListToolsResult, MCPCallToolResult,
    JSONRPCRequest, JSONRPCResponse, JSONRPCError,
)
from app_v4.tools.registry import TOOL_BY_NAME, get_tool_permission, get_tools
from app_v4.safety.guard import SafetyGuard
from app_v4.model.command_runner import CommandRunner


class MCPServer:
    """MCP JSON-RPC Server — 不依赖 HTTP，可嵌入任何传输。"""

    def __init__(self) -> None:
        self._guard = SafetyGuard()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """处理 JSON-RPC 请求，返回 JSON-RPC 响应。"""
        try:
            req = JSONRPCRequest(**request)
        except Exception as exc:
            return self._error(None, -32600, f"Invalid request: {exc}")

        if req.method == "tools/list":
            result = self._tools_list()
        elif req.method == "tools/call":
            result = self._tools_call(req.params)
        else:
            return self._error(req.id, -32601, f"Method not found: {req.method}")

        return JSONRPCResponse(id=req.id, result=result.model_dump()).model_dump()

    # ------------------------------------------------------------------
    def _tools_list(self) -> MCPListToolsResult:
        """返回所有工具信息。"""
        tools: list[MCPToolInfo] = []
        for tool in get_tools():
            # 从 LangChain @tool 对象提取 schema
            tool_obj = TOOL_BY_NAME.get(tool.name)
            input_schema = {}
            if tool_obj and hasattr(tool_obj, "args_schema") and tool_obj.args_schema:
                try:
                    input_schema = tool_obj.args_schema.model_json_schema()
                except Exception:
                    input_schema = {}
            elif tool_obj and hasattr(tool_obj, "args") and tool_obj.args:
                input_schema = dict(tool_obj.args)

            tools.append(MCPToolInfo(
                name=tool.name,
                description=tool.description or "",
                inputSchema=input_schema,
                riskLevel="medium" if get_tool_permission(tool.name) == "confirm" else "low",
                permission=get_tool_permission(tool.name),
            ))
        return MCPListToolsResult(tools=tools)

    def _tools_call(self, params: dict[str, Any]) -> MCPCallToolResult:
        """调用单个工具（经过安全预检）。"""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # 1. 工具存在性检查
        tool = TOOL_BY_NAME.get(tool_name)
        if tool is None:
            return MCPCallToolResult(
                content=[{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                isError=True,
            )

        # 2. 权限检查（deny 策略的工具拒绝）
        permission = get_tool_permission(tool_name)
        if permission == "deny":
            return MCPCallToolResult(
                content=[{"type": "text",
                         "text": f"Tool '{tool_name}' is denied by policy"}],
                isError=True,
            )
        if permission == "confirm":
            return MCPCallToolResult(
                content=[{"type": "text",
                         "text": f"Tool '{tool_name}' requires approval (confirm policy)"}],
                isError=True,
            )

        # 3. 参数安全扫描（注入检测）
        args_text = json.dumps(arguments, ensure_ascii=False)
        scan = self._guard.scan_untrusted_output(
            {"tool_name": tool_name, "result": {"args": args_text}})
        if scan["detected"]:
            return MCPCallToolResult(
                content=[{"type": "text",
                         "text": f"Blocked: prompt injection detected in arguments: {scan['reasons']}"}],
                isError=True,
            )

        # 4. 执行
        t0 = time.monotonic()
        try:
            result = tool.invoke(arguments)
            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            result["_mcp_duration_ms"] = duration_ms
            return MCPCallToolResult(
                content=[{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                isError=False,
            )
        except Exception as exc:
            return MCPCallToolResult(
                content=[{"type": "text", "text": f"Tool error: {exc}"}],
                isError=True,
            )

    # ------------------------------------------------------------------
    def _error(self, req_id: Any, code: int, message: str) -> dict[str, Any]:
        return JSONRPCResponse(
            id=req_id,
            error=JSONRPCError(code=code, message=message),
        ).model_dump()
