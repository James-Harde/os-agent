import json
import re
import unicodedata
from typing import Any


class SafetyGuard:
    RISK_ORDER = {"low": 1, "medium": 2, "high": 3}

    # Zero-width characters and Unicode control codes used to bypass regex filters.
    _ZERO_WIDTH = "​-‍﻿"

    HIGH_RISK_PATTERNS = [
        (r"\brm\s+-rf\b", "检测到递归强制删除命令"),
        (r"\br\s*-\s*rf\b", "检测到被空白分隔的递归删除命令（零宽字符/空格混淆）"),
        (r"\bchmod\s+777\b", "检测到过宽权限修改"),
        (r"\bchown\s+-R\b", "检测到递归属主修改"),
        (r"\bmkfs\b", "检测到格式化磁盘命令"),
        (r"\bdd\s+if=", "检测到块设备写入命令"),
        (r"\bshutdown\b|\breboot\b", "检测到关机或重启命令"),
        (r"删除所有|清空.*日志|关闭防火墙|格式化", "检测到高危自然语言运维意图"),
        (r"`[^`]{1,200}`", "检测到反引号子shell执行（command substitution）"),
        (r"\$\([^)]{1,200}\)", "检测到命令替换执行（$(...) 形式）"),
    ]

    PROMPT_INJECTION_PATTERNS = [
        (r"忽略之前", "疑似要求忽略已有规则"),
        (r"ignore\s+(all\s+)?previous", "疑似英文提示词注入"),
        (r"system\s+prompt", "疑似系统提示词探测"),
        (r"直接执行\s+rm\s+-rf", "疑似诱导执行破坏性命令"),
        (r"不要告诉管理员", "疑似规避审计意图"),
    ]

    ANALYSIS_VERBS = ["分析", "检测", "检查", "扫描", "analyze", "scan", "check"]
    UNTRUSTED_TEXT_MARKERS = ["这段", "以下", "日志：", "日志:", "log:", "log："]

    def preflight_request(self, user_input: str) -> dict[str, Any]:
        """Block direct dangerous requests before LLM planning.

        If the user is asking to analyze a log/snippet that contains dangerous text,
        keep it in the analysis path so it can be treated as untrusted data.
        """
        high_risk_reasons = self._detect_high_risk(user_input)
        injection_reasons = self._detect_prompt_injection(user_input)

        if self._looks_like_untrusted_text_analysis(user_input) and (
            high_risk_reasons or injection_reasons
        ):
            return self._decision(
                "high",
                "allow",
                ["输入包含高危/注入文本，但语境是分析不可信数据，不进入执行路径"],
            )

        if high_risk_reasons:
            return self._decision("high", "deny", high_risk_reasons)

        if injection_reasons:
            return self._decision("high", "deny", injection_reasons)

        return self._decision("low", "allow", ["请求未命中预检高危规则"])

    def assess_request(self, user_input: str, intent: str) -> dict[str, Any]:
        high_risk_reasons = self._detect_high_risk(user_input)
        injection_reasons = self._detect_prompt_injection(user_input)

        if injection_reasons and intent == "prompt_injection_analysis":
            return self._decision("high", "allow", injection_reasons)

        if high_risk_reasons:
            return self._decision("high", "deny", high_risk_reasons)

        if injection_reasons:
            return self._decision("high", "deny", injection_reasons)

        return self._decision("low", "allow", ["请求未命中高危规则"])

    def assess_plan(
        self,
        plan: list[dict[str, Any]],
        tool_specs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        reasons = []
        risk_level = "low"
        tool_specs = tool_specs or {}
        for step in plan:
            tool = step.get("tool", "")
            spec = tool_specs.get(tool)
            if not spec:
                risk_level = "high"
                reasons.append(f"计划包含未注册工具：{tool}")
                continue

            execution_mode = spec.get("execution_mode")
            permission = spec.get("permission")
            read_only = spec.get("read_only")
            tool_risk = spec.get("risk_level")

            if execution_mode == "auto" and permission == "read" and read_only and tool_risk == "low":
                continue

            if execution_mode == "confirm":
                if self.RISK_ORDER["medium"] > self.RISK_ORDER[risk_level]:
                    risk_level = "medium"
                reasons.append(f"工具需要人工确认，当前不会自动执行：{tool}")
                continue

            if execution_mode == "deny":
                risk_level = "high"
                reasons.append(f"工具被当前沙盒策略禁止：{tool}")
                continue

            risk_level = "high"
            reasons.append(f"工具权限策略不满足自动执行条件：{tool}")

        if reasons:
            return self._decision(risk_level, "deny", reasons)
        return self._decision("low", "allow", ["工具计划均为只读或安全扫描能力"])

    def scan_untrusted_output(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(tool_call.get("result", {}), ensure_ascii=False)
        reasons = self._detect_prompt_injection(payload)
        return {
            "detected": bool(reasons),
            "risk_level": "high" if reasons else "low",
            "source_tool": tool_call.get("tool_name"),
            "reasons": reasons,
        }

    def from_output_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        if not events:
            return self._decision("low", "allow", ["工具输出未发现注入风险"])
        reasons = []
        for event in events:
            source = event.get("source_tool", "unknown")
            for reason in event.get("reasons", []):
                reasons.append(f"{source}: {reason}")
        return self._decision("high", "allow", reasons)

    def merge(self, *decisions: dict[str, Any]) -> dict[str, Any]:
        risk_level = "low"
        final_decision = "allow"
        reasons: list[str] = []

        for decision in decisions:
            if self.RISK_ORDER[decision["risk_level"]] > self.RISK_ORDER[risk_level]:
                risk_level = decision["risk_level"]
            if decision["decision"] == "deny":
                final_decision = "deny"
            reasons.extend(decision.get("reasons", []))

        return self._decision(risk_level, final_decision, reasons)

    def _detect_high_risk(self, text: str) -> list[str]:
        text = self._normalize(text)
        reasons = []
        for pattern, reason in self.HIGH_RISK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                reasons.append(reason)
        return reasons

    def _detect_prompt_injection(self, text: str) -> list[str]:
        text = self._normalize(text)
        reasons = []
        for pattern, reason in self.PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                reasons.append(reason)
        return reasons

    @staticmethod
    def _normalize(text: str) -> str:
        """Unicode normalization + zero-width character removal.

        Purpose:
            Strip homoglyphs, zero-width joiners, and invisible modifiers before
            regex matching. Without this, an attacker can bypass the SafetyGuard
            by inserting U+200B (zero-width space) between characters, e.g.
            "r​m -​ rf" would not match the original ``rm\\s+-rf`` pattern.

        Steps:
            1. NFKC fold full-width letters/digits and compatibility variants.
            2. Delete zero-width chars (U+200B-U+200D, U+FEFF).
        """
        text = unicodedata.normalize("NFKC", text)
        return text.translate({ord(c): None for c in SafetyGuard._ZERO_WIDTH})

    def _looks_like_untrusted_text_analysis(self, text: str) -> bool:
        lowered = text.lower()
        has_analysis_verb = any(verb in lowered for verb in self.ANALYSIS_VERBS)
        has_text_marker = any(marker in lowered for marker in self.UNTRUSTED_TEXT_MARKERS)
        return has_analysis_verb and has_text_marker

    def _decision(self, risk_level: str, decision: str, reasons: list[str]) -> dict[str, Any]:
        return {
            "risk_level": risk_level,
            "decision": decision,
            "reasons": list(dict.fromkeys(reasons)),
        }
