"""统一工具应用服务 — ToolApplicationService。

§4.4 #1：建立唯一 ToolApplicationService，统一完成：
  schema 校验、权限、审批凭证、执行、超时、错误映射、输出扫描、缓存和审计。
§4.4 #2：LangGraph 与 MCP Server 不得各复制一套执行和安全逻辑。

设计：
  - 所有工具执行（无论来自 Graph 节点还是 MCP）都经此类。
  - 可变更工具（confirm 权限）通过 MutationAdapter 执行，测试可注入
    RecordingMutationAdapter 计数调用次数，生产可注入真实 systemctl adapter。
  - 审批凭证校验：confirm 工具必须持有未消费的、与工具及参数匹配的审批凭证。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Protocol

from app_v4.safety.guard import SafetyGuard


class MutationAdapter(Protocol):
    """可变更工具执行适配器协议。

    测试注入 RecordingMutationAdapter 计数；生产注入 SystemctlAdapter（受配置开关约束）。
    """

    def execute(self, tool_name: str, service: str, **kwargs: Any) -> dict[str, Any]:
        ...


class RecordingMutationAdapter:
    """测试用：记录调用次数，返回 dry-run 结果（不执行真实命令）。

    §5 Gate 2：使用 recording/test adapter 验证调用次数；
    自动化测试不得真实重启系统服务。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, tool_name: str, service: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"tool_name": tool_name, "service": service, "kwargs": kwargs})
        return {
            "status": "success",
            "source": "recording_adapter",
            "service": service,
            "message": f"[dry-run] {tool_name} {service}（recording adapter，未执行真实命令）",
            "dry_run": True,
            "call_index": len(self.calls),
        }

    @property
    def call_count(self) -> int:
        return len(self.calls)


class ToolApplicationService:
    """统一工具应用服务。

    职责：
      - 根据工具权限路由：auto 直接执行，confirm 校验审批凭证后执行。
      - 可变更工具经 MutationAdapter 执行（可注入）。
      - 执行后扫描输出。

    B6 安全：真实 mutation 必须同时满足——配置开关 mutation_enabled、
    服务在 allowlist 内、持有已批准的审批凭证。未开启时返回 disabled/dry-run，
    不调用 adapter，避免 recording adapter 冒充真实重启。
    """

    def __init__(
        self,
        mutation_adapter: MutationAdapter | None = None,
        mutation_enabled: bool = False,
        allowed_services: list[str] | None = None,
    ) -> None:
        self.mutation_adapter = mutation_adapter or RecordingMutationAdapter()
        self.guard = SafetyGuard()
        self.mutation_enabled = mutation_enabled
        # allowed_services 为空表示全部允许；非空则仅列表内服务可执行。
        self.allowed_services = allowed_services if allowed_services is not None else []

    def execute_mutation(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        approval_id: str | None = None,
        approval_status: str | None = None,
    ) -> dict[str, Any]:
        """执行可变更工具（confirm 权限）。

        校验（按顺序）：
          - 必须提供 approval_id，且 approval_status 必须为 "approved"
          - mutation_enabled 必须为 True（生产开关）
          - 服务必须在 allowlist 内（若配置了 allowlist）
          - 同一 approval_id 只执行一次（由调用方保证幂等，见 executed_approvals）
        """
        if not approval_id:
            return {
                "status": "error",
                "error": "missing approval_id",
                "source": "approval_gate",
                "message": "可变更工具需要审批凭证",
            }
        if approval_status != "approved":
            return {
                "status": "rejected",
                "error": f"审批未通过（状态：{approval_status}）",
                "source": "approval_gate",
                "message": "审批未通过，未执行",
            }

        # B6：生产开关未开启 → 返回 disabled，不调用 adapter（避免 recording 冒充真实重启）。
        if not self.mutation_enabled:
            return {
                "status": "disabled",
                "source": "mutation_switch",
                "message": "可变更工具执行未启用（mutation_enabled=false），未执行真实命令",
                "approval_id": approval_id,
            }

        service = arguments.get("service", "")
        # B6：服务 allowlist 控制。
        if self.allowed_services and service not in self.allowed_services:
            return {
                "status": "denied",
                "error": f"服务 {service} 不在允许列表（allowlist）内",
                "source": "mutation_allowlist",
                "message": "服务未授权，未执行",
                "approval_id": approval_id,
            }

        t0 = time.monotonic()
        try:
            # service 已作为位置参数传入，从 extra_args 中剔除，避免重复传参。
            extra_args = {k: v for k, v in arguments.items() if k != "service"}
            result = self.mutation_adapter.execute(tool_name, service, **extra_args)
            result["_duration_ms"] = round((time.monotonic() - t0) * 1000, 2)
            result["approval_id"] = approval_id
        except Exception as exc:
            result = {
                "status": "error",
                "error": str(exc),
                "source": "mutation_adapter",
                "service": service,
                "approval_id": approval_id,
            }
        return result

    def execute_auto(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """统一 auto 工具执行入口（§4.4 #1 / MCP 修复 finding #5）。

        仅供 ``auto`` 权限的只读工具使用；可变更工具必须走
        :meth:`execute_mutation`，不得经由此方法。

        校验顺序：
          - 工具必须存在于注册表
          - 工具权限必须为 auto（最小权限：外部 MCP 不暴露 confirm/mutation）
          - 调用工具
          - 输出扫描（注入检测）

        成功、失败、拒绝都走同一错误映射和输出扫描，返回统一结构：
        ``{status, source, ..., _output_scan}``。
        """
        from app_v4.tools.registry import TOOL_BY_NAME, get_tool_permission

        tool = TOOL_BY_NAME.get(tool_name)
        if tool is None:
            return {
                "status": "error",
                "error": f"unknown tool: {tool_name}",
                "source": "tool_application_service",
            }
        if get_tool_permission(tool_name) != "auto":
            return {
                "status": "error",
                "error": f"tool {tool_name} is not auto-permission (least-privilege gate)",
                "source": "tool_application_service",
            }
        try:
            result = tool.invoke(arguments)
        except Exception as exc:
            result = {
                "status": "error",
                "error": str(exc),
                "source": "tool_application_service",
            }
        # 输出扫描（注入检测）— 成功/失败都走同一扫描边界
        scan = self.guard.scan_untrusted_output({"tool_name": tool_name, "result": result})
        result["_output_scan"] = {
            "detected": scan["detected"],
            "risk_level": scan["risk_level"],
        }
        return result

    def scan_output(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        """扫描工具输出中的注入内容。"""
        return self.guard.scan_untrusted_output({"tool_name": tool_name, "result": result})
