"""SafetyGuard — 和 v2 一样的纯检查逻辑。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


class SafetyGuard:
    HIGH_RISK_PATTERNS = [
        (r"\brm\s+-rf\b", "检测到递归强制删除"),
        (r"\br\s*-\s*rf\b", "检测到混淆的递归删除"),
        (r"\bchmod\s+777\b", "检测到过宽权限修改"),
        (r"\bchown\s+-R\b", "检测到递归属主修改"),
        (r"\bmkfs\b", "检测到格式化命令"),
        (r"\bdd\s+if=", "检测到块设备写入"),
        (r"\bshutdown\b|\breboot\b", "检测到关机/重启"),
        (r"删除所有|清空.*日志|关闭防火墙|格式化", "检测到高危运维意图"),
        (r"`[^`]{1,200}`", "检测到反引号子shell"),
        (r"\$\([^)]{1,200}\)", "检测到命令替换"),
    ]
    PROMPT_INJECTION_PATTERNS = [
        (r"忽略之前", "疑似忽略规则"),
        (r"ignore\s+(all\s+)?previous", "疑似英文注入"),
        (r"system\s+prompt", "疑似探测系统提示词"),
        (r"直接执行\s+rm\s+-rf", "疑似诱导破坏性命令"),
        (r"不要告诉管理员", "疑似规避审计"),
    ]
    ANALYSIS_VERBS = ["分析", "检测", "检查", "扫描", "analyze", "scan", "check"]
    UNTRUSTED_MARKERS = ["这段", "以下", "日志：", "日志:", "log:", "log："]

    def check_input(self, user_input: str) -> dict[str, Any]:
        high = self._detect(self.HIGH_RISK_PATTERNS, user_input)
        inj = self._detect(self.PROMPT_INJECTION_PATTERNS, user_input)
        is_analysis = self._is_analysis(user_input)
        if is_analysis and (high or inj):
            return {"risk_level": "high",
                    "reasons": ["高危文本作为数据分析，不进入执行路径"],
                    "is_analysis_context": True}
        reasons = high + inj
        return {"risk_level": "high" if reasons else "low",
                "reasons": reasons or ["未命中高危规则"],
                "is_analysis_context": False}

    def check_plan(self, plan: list[dict], allowed: set[str]) -> dict[str, Any]:
        for step in plan:
            if step.get("tool") not in allowed:
                return {"risk_level": "high",
                        "reasons": [f"未授权工具：{step.get('tool')}"]}
        return {"risk_level": "low", "reasons": ["计划工具均在白名单"]}

    def scan_untrusted_output(self, tool_call: dict) -> dict[str, Any]:
        import json
        payload = json.dumps(tool_call.get("result", {}), ensure_ascii=False)
        reasons = self._detect(self.PROMPT_INJECTION_PATTERNS, payload)
        return {"detected": bool(reasons),
                "risk_level": "high" if reasons else "low",
                "source_tool": tool_call.get("tool_name"), "reasons": reasons}

    def _detect(self, patterns, text):
        text = self._normalize(text)
        return [r for p, r in patterns if re.search(p, text, re.IGNORECASE)]

    def _is_analysis(self, text):
        t = text.lower()
        return any(v in t for v in self.ANALYSIS_VERBS) and any(m in t for m in self.UNTRUSTED_MARKERS)

    @staticmethod
    def _normalize(text):
        text = unicodedata.normalize("NFKC", text)
        return text.translate({ord(c): None for c in "​-‍﻿"})
