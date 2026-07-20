"""安全检查函数 — 纯检查器，不做路由决策。

对比 app_v2：逻辑不变，保持 NFKC 归零宽字符防混淆。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


class SafetyGuard:
    """纯检查器。只回答"有没有危险"，不回答"接下来怎么办"。"""

    HIGH_RISK_PATTERNS = [
        (r"\brm\s+-rf\b", "检测到递归强制删除命令"),
        (r"\br\s*-\s*rf\b", "检测到被空白分隔的递归删除（混淆）"),
        (r"\bchmod\s+777\b", "检测到过宽权限修改"),
        (r"\bchown\s+-R\b", "检测到递归属主修改"),
        (r"\bmkfs\b", "检测到格式化磁盘命令"),
        (r"\bdd\s+if=", "检测到块设备写入命令"),
        (r"\bshutdown\b|\breboot\b", "检测到关机或重启命令"),
        (r"删除所有|清空.*日志|关闭防火墙|格式化", "检测到高危自然语言运维意图"),
        (r"`[^`]{1,200}`", "检测到反引号子shell"),
        (r"\$\([^)]{1,200}\)", "检测到命令替换"),
    ]

    PROMPT_INJECTION_PATTERNS = [
        (r"忽略之前", "疑似要求忽略已有规则"),
        (r"ignore\s+(all\s+)?previous", "疑似英文提示词注入"),
        (r"system\s+prompt", "疑似系统提示词探测"),
        (r"直接执行\s+rm\s+-rf", "疑似诱导执行破坏性命令"),
        (r"不要告诉管理员", "疑似规避审计意图"),
    ]

    ANALYSIS_VERBS = ["分析", "检测", "检查", "扫描", "analyze", "scan", "check"]
    UNTRUSTED_MARKERS = ["这段", "以下", "日志：", "日志:", "log:", "log："]

    def check_input(self, user_input: str) -> dict[str, Any]:
        """检查用户输入。"""
        high_risk = self._detect(self.HIGH_RISK_PATTERNS, user_input)
        injection = self._detect(self.PROMPT_INJECTION_PATTERNS, user_input)
        is_analysis = self._is_analysis_context(user_input)

        # 分析语境：高危文本作为数据不进执行路径
        if is_analysis and (high_risk or injection):
            return {
                "risk_level": "low",
                "reasons": ["输入包含高危文本，但语境为分析不可信数据，不作为命令执行"],
                "is_analysis_context": True,
            }

        reasons = high_risk + injection
        return {
            "risk_level": "high" if reasons else "low",
            "reasons": reasons or ["未命中高危规则"],
            "is_analysis_context": False,
        }

    def check_plan(self, plan: list[dict], allowed_tool_names: set[str]) -> dict[str, Any]:
        """检查 LLM 计划是否在白名单内。"""
        for step in plan:
            tool = step.get("tool", "")
            if tool not in allowed_tool_names:
                return {"risk_level": "high", "reasons": [f"计划包含未授权工具：{tool}"]}
        return {"risk_level": "low", "reasons": ["计划工具均在白名单内"]}

    def scan_untrusted_output(self, tool_call: dict) -> dict[str, Any]:
        """扫描工具输出中的注入内容。"""
        import json
        payload = json.dumps(tool_call.get("result", {}), ensure_ascii=False)
        reasons = self._detect(self.PROMPT_INJECTION_PATTERNS, payload)
        return {
            "detected": bool(reasons),
            "risk_level": "high" if reasons else "low",
            "source_tool": tool_call.get("tool_name"),
            "reasons": reasons,
        }

    def scan_final_answer(self, answer: str) -> dict[str, Any]:
        """扫描最终回答中的恶意指令（确定性输出阻断，修复 audit #9）。

        工具输出中的攻击指令即使进入 summarizer，最终回答也不得原样输出
        或服从攻击指令（如"rm -rf /"、"忽略规则"）。
        """
        reasons = self._detect(self.PROMPT_INJECTION_PATTERNS, answer)
        # 额外检查：高危命令是否出现在回答中
        high_risk_in_answer = self._detect(
            [(r"\brm\s+-rf\b", "回答含递归删除命令"),
             (r"\bchmod\s+777\b", "回答含危险权限修改"),
             (r"\bmkfs\b", "回答含格式化命令")],
            answer,
        )
        all_reasons = reasons + high_risk_in_answer
        return {
            "detected": bool(all_reasons),
            "risk_level": "high" if all_reasons else "low",
            "reasons": all_reasons,
        }

    def _detect(self, patterns: list[tuple[str, str]], text: str) -> list[str]:
        text = self._normalize(text)
        return [reason for pat, reason in patterns if re.search(pat, text, re.IGNORECASE)]

    def _is_analysis_context(self, text: str) -> bool:
        lowered = text.lower()
        return (
            any(v in lowered for v in self.ANALYSIS_VERBS)
            and any(m in lowered for m in self.UNTRUSTED_MARKERS)
        )

    @staticmethod
    def _normalize(text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        # 只移除零宽字符（不要包含普通连字符 -）
        zero_width = "​‌‍﻿"
        return text.translate({ord(c): None for c in zero_width})
