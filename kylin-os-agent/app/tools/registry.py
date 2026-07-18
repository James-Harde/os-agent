from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.audit.logger import AuditLogger
from app.tools import system_tools
from app.tools.types import ToolSpec


class ToolRegistry:
    def __init__(self, audit_logger: AuditLogger) -> None:
        self.audit_logger = audit_logger
        self._tools: dict[str, ToolSpec] = {}
        self._register_builtin_tools()

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def list_specs(self) -> list[dict[str, Any]]:
        return [spec.public_dict() for spec in self._tools.values()]

    def sandbox_status(self) -> dict[str, Any]:
        specs = self.list_specs()
        return {
            "enabled": True,
            "mode": "application_sandbox",
            "policy": "only read/auto tools can execute; confirm/deny tools are blocked",
            "shell": "disabled",
            "auto_tools": [spec["name"] for spec in specs if spec["execution_mode"] == "auto"],
            "confirm_tools": [spec["name"] for spec in specs if spec["execution_mode"] == "confirm"],
            "deny_tools": [spec["name"] for spec in specs if spec["execution_mode"] == "deny"],
        }

    def spec_map(self) -> dict[str, dict[str, Any]]:
        return {name: spec.public_dict() for name, spec in self._tools.items()}

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        request_id: str,
        reason: str,
    ) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat()
        spec = self._tools.get(name)
        if not spec:
            result = {"status": "error", "error": f"tool not found: {name}"}
            status = "error"
            risk_level = "high"
        elif spec.execution_mode != "auto" or not spec.read_only or spec.permission != "read":
            result = {
                "status": "blocked",
                "error": "tool is not allowed to run automatically in current sandbox",
                "execution_mode": spec.execution_mode,
                "permission": spec.permission,
                "read_only": spec.read_only,
            }
            status = "blocked"
            risk_level = spec.risk_level
        else:
            try:
                result = spec.handler(arguments)
                status = result.get("status", "ok")
                risk_level = spec.risk_level
            except Exception as exc:  # Keep one bad tool from breaking the demo.
                result = {"status": "error", "error": str(exc)}
                status = "error"
                risk_level = spec.risk_level

        finished_at = datetime.now(timezone.utc).isoformat()
        self.audit_logger.record_tool_call(
            request_id=request_id,
            tool_name=name,
            arguments=arguments,
            result=result,
            risk_level=risk_level,
            status=status,
            reason=reason,
        )
        return {
            "tool_name": name,
            "arguments": arguments,
            "reason": reason,
            "risk_level": risk_level,
            "permission": spec.permission if spec else "unknown",
            "execution_mode": spec.execution_mode if spec else "deny",
            "sandbox_scope": spec.sandbox_scope if spec else "none",
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "result": result,
        }

    def _register_builtin_tools(self) -> None:
        self.register(
            ToolSpec(
                name="disk_usage",
                description="获取磁盘使用率",
                risk_level="low",
                permission="read",
                read_only=True,
                execution_mode="auto",
                sandbox_scope="system_readonly",
                allowed_commands=(),
                handler=system_tools.disk_usage,
            )
        )
        self.register(
            ToolSpec(
                name="directory_usage",
                description="获取目录占用排行",
                risk_level="low",
                permission="read",
                read_only=True,
                execution_mode="auto",
                sandbox_scope="project_readonly",
                allowed_commands=(),
                handler=system_tools.directory_usage,
            )
        )
        self.register(
            ToolSpec(
                name="port_lookup",
                description="查询端口占用",
                risk_level="low",
                permission="read",
                read_only=True,
                execution_mode="auto",
                sandbox_scope="network_readonly",
                allowed_commands=("ss", "netstat", "lsof"),
                handler=system_tools.port_lookup,
            )
        )
        self.register(
            ToolSpec(
                name="process_list",
                description="查询进程列表",
                risk_level="low",
                permission="read",
                read_only=True,
                execution_mode="auto",
                sandbox_scope="process_readonly",
                allowed_commands=("ps", "tasklist"),
                handler=system_tools.process_list,
            )
        )
        self.register(
            ToolSpec(
                name="system_logs",
                description="读取系统告警和错误日志",
                risk_level="low",
                permission="read",
                read_only=True,
                execution_mode="auto",
                sandbox_scope="logs_readonly",
                allowed_commands=("journalctl",),
                handler=system_tools.system_logs,
            )
        )
        self.register(
            ToolSpec(
                name="service_status",
                description="查询服务状态",
                risk_level="low",
                permission="read",
                read_only=True,
                execution_mode="auto",
                sandbox_scope="service_readonly",
                allowed_commands=("systemctl",),
                handler=system_tools.service_status,
            )
        )
        self.register(
            ToolSpec(
                name="prompt_injection_scan",
                description="扫描不可信文本中的提示词注入风险",
                risk_level="low",
                permission="read",
                read_only=True,
                execution_mode="auto",
                sandbox_scope="text_scan",
                allowed_commands=(),
                handler=system_tools.prompt_injection_scan,
            )
        )
        self.register(
            ToolSpec(
                name="service_restart",
                description="重启服务，需要人工确认，当前沙盒不自动执行",
                risk_level="medium",
                permission="confirm",
                read_only=False,
                execution_mode="confirm",
                sandbox_scope="blocked_mutation",
                allowed_commands=(),
                handler=system_tools.blocked_operation,
            )
        )
        self.register(
            ToolSpec(
                name="file_delete",
                description="删除文件，高危操作，当前沙盒禁止执行",
                risk_level="high",
                permission="deny",
                read_only=False,
                execution_mode="deny",
                sandbox_scope="blocked_destructive",
                allowed_commands=(),
                handler=system_tools.blocked_operation,
            )
        )
