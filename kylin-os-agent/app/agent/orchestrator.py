#主要负责把多个模块串起来。组装agent
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.approval.service import ApprovalService
from app.audit.logger import AuditLogger
from app.memory.store import MemoryStore
from app.model.adapter import ModelAdapter
from app.safety.guard import SafetyGuard
from app.tools.registry import ToolRegistry


class AgentOrchestrator:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        safety_guard: SafetyGuard,
        audit_logger: AuditLogger,
        memory_store: MemoryStore,
        model_adapter: ModelAdapter,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.safety_guard = safety_guard
        self.audit_logger = audit_logger
        self.memory_store = memory_store
        self.model_adapter = model_adapter
        self.approval_service = approval_service

    def handle(self, user_input: str, conversation_id: str | None = None) -> dict[str, Any]:
        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("message cannot be empty")

        request_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        conversation_id = self.memory_store.ensure_conversation(
            conversation_id=conversation_id,
            title=normalized_input,
        )
        memory = self.memory_store.recent_messages(conversation_id)
        self.memory_store.add_message(
            conversation_id=conversation_id,
            role="user",
            content=normalized_input,
            metadata={"request_id": request_id},
        )

        preflight_guard = self.safety_guard.preflight_request(normalized_input)
        if preflight_guard["decision"] == "deny":
            intent = "dangerous_operation"
            plan: list[dict[str, Any]] = []
            planning = {
                "intent": intent,
                "plan": plan,
                "planner_source": "safety_preflight",
                "planner_notes": "高危请求在 LLM 规划前被安全护栏拦截。",
            }
            answer = self.model_adapter.explain_denial(
                user_input=normalized_input,
                intent=intent,
                guard=preflight_guard,
            )
            self.audit_logger.record_audit_log(
                request_id=request_id,
                user_input=normalized_input,
                intent=intent,
                plan=plan,
                risk_level=preflight_guard["risk_level"],
                guard_decision=preflight_guard["decision"],
                final_answer=answer,
            )
            self.memory_store.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                metadata={
                    "request_id": request_id,
                    "intent": intent,
                    "risk_level": preflight_guard["risk_level"],
                    "guard_decision": preflight_guard["decision"],
                    "planner_source": planning["planner_source"],
                },
            )
            return self._response(
                request_id=request_id,
                conversation_id=conversation_id,
                started_at=started_at,
                intent=intent,
                plan=plan,
                planning=planning,
                guard=preflight_guard,
                tool_calls=[],
                output_guard_events=[],
                answer=answer,
                answer_source="safety_template",
            )

        planning = self.model_adapter.plan(
            user_input=normalized_input,
            memory=memory,
            tools=self.tool_registry.list_specs(),
        )
        intent = planning["intent"]
        plan = planning["plan"]

        request_guard = self.safety_guard.assess_request(normalized_input, intent)
        plan_guard = self.safety_guard.assess_plan(plan, self.tool_registry.spec_map())
        merged_guard = self.safety_guard.merge(request_guard, plan_guard)

        tool_calls: list[dict[str, Any]] = []
        output_guard_events: list[dict[str, Any]] = []

        if merged_guard["decision"] == "deny":
            answer = self.model_adapter.explain_denial(
                user_input=normalized_input,
                intent=intent,
                guard=merged_guard,
            )
            self.audit_logger.record_audit_log(
                request_id=request_id,
                user_input=normalized_input,
                intent=intent,
                plan=plan,
                risk_level=merged_guard["risk_level"],
                guard_decision=merged_guard["decision"],
                final_answer=answer,
            )
            self.memory_store.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                metadata={
                    "request_id": request_id,
                    "intent": intent,
                    "risk_level": merged_guard["risk_level"],
                    "guard_decision": merged_guard["decision"],
                    "planner_source": planning["planner_source"],
                },
            )
            return self._response(
                request_id=request_id,
                conversation_id=conversation_id,
                started_at=started_at,
                intent=intent,
                plan=plan,
                planning=planning,
                guard=merged_guard,
                tool_calls=[],
                output_guard_events=[],
                answer=answer,
                answer_source="safety_template",
            )

        for step in plan:
            tool_name = step["tool"]
            tool_spec = self.tool_registry.spec_map().get(tool_name)
            execution_mode = (tool_spec or {}).get("execution_mode", "deny")

            if execution_mode != "auto" and self.approval_service is not None:
                # confirm / deny tools: do not run. Create an approval card instead.
                approval_id = self.approval_service.create(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    arguments=step["arguments"],
                )
                blocker_result = {
                    "status": "blocked_pending_approval",
                    "error": f"工具 '{tool_name}' 需要人工审批后才能执行",
                    "approval_id": approval_id,
                    "tool_name": tool_name,
                    "arguments": step["arguments"],
                    "reason": step["reason"],
                    "risk_level": (tool_spec or {}).get("risk_level", "medium"),
                    "permission": (tool_spec or {}).get("permission", "confirm"),
                    "execution_mode": execution_mode,
                }
                tool_calls.append(blocker_result)
                continue

            tool_call = self.tool_registry.call(
                name=tool_name,
                arguments=step["arguments"],
                request_id=request_id,
                reason=step["reason"],
            )
            tool_calls.append(tool_call)
            output_guard = self.safety_guard.scan_untrusted_output(tool_call)
            if output_guard["detected"]:
                output_guard_events.append(output_guard)

        final_guard = self.safety_guard.merge(
            merged_guard,
            self.safety_guard.from_output_events(output_guard_events),
        )
        answer = self.model_adapter.summarize(
            user_input=normalized_input,
            intent=intent,
            plan=plan,
            guard=final_guard,
            tool_calls=tool_calls,
            output_guard_events=output_guard_events,
            memory=memory,
        )

        self.audit_logger.record_audit_log(
            request_id=request_id,
            user_input=normalized_input,
            intent=intent,
            plan=plan,
            risk_level=final_guard["risk_level"],
            guard_decision=final_guard["decision"],
            final_answer=answer,
        )
        self.memory_store.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=answer,
            metadata={
                "request_id": request_id,
                "intent": intent,
                "risk_level": final_guard["risk_level"],
                "guard_decision": final_guard["decision"],
                "planner_source": planning["planner_source"],
                "tool_count": len(tool_calls),
            },
        )

        return self._response(
            request_id=request_id,
            conversation_id=conversation_id,
            started_at=started_at,
            intent=intent,
            plan=plan,
            planning=planning,
            guard=final_guard,
            tool_calls=tool_calls,
            output_guard_events=output_guard_events,
            answer=answer,
            answer_source="llm_summary",
        )

    def _response(
        self,
        request_id: str,
        conversation_id: str,
        started_at: str,
        intent: str,
        plan: list[dict[str, Any]],
        planning: dict[str, Any],
        guard: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        output_guard_events: list[dict[str, Any]],
        answer: str,
        answer_source: str,
    ) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "started_at": started_at,
            "intent": intent,
            "plan": plan,
            "planner_source": planning["planner_source"],
            "planner_notes": planning.get("planner_notes", ""),
            "planner_error": planning.get("planner_error"),
            "model": self.model_adapter.status(),
            "risk_level": guard["risk_level"],
            "guard_decision": guard["decision"],
            "guard_reasons": guard["reasons"],
            "tool_calls": tool_calls,
            "output_guard_events": output_guard_events,
            "answer": answer,
            "answer_source": answer_source,
        }
