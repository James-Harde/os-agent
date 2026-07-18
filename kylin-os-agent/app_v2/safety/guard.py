"""安全检查函数 — 变薄了。

教学要点：
  旧版 SafetyGuard 既做"检查"又做"路由决策"（return allow/deny 给 orchestrator，
  orchestrator 根据 deny 决定跳过工具执行）。

  新版拆开了：
    - Guard 只负责"检查逻辑"（不决策流程怎么走）
    - 路由决策交给 LangGraph 的条件边（edges.py）

  Guard 变成了纯函数：输入文本，输出检测结果。
  图的条件边拿到检测结果后，决定接下来走哪个节点。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


class SafetyGuard:
    """纯检查器。只回答"这里有没有危险"，不回答"接下来怎么办"。"""

    # 高危命令模式
    HIGH_RISK_PATTERNS = [
        (r"\brm\s+-rf\b", "检测到递归强制删除命令"),
        (r"\br\s*-\s*rf\b", "检测到被空白分隔的递归删除命令（混淆）"),
        (r"\bchmod\s+777\b", "检测到过宽权限修改"),
        (r"\bchown\s+-R\b", "检测到递归属主修改"),
        (r"\bmkfs\b", "检测到格式化磁盘命令"),
        (r"\bdd\s+if=", "检测到块设备写入命令"),
        (r"\bshutdown\b|\breboot\b", "检测到关机或重启命令"),
        (r"删除所有|清空.*日志|关闭防火墙|格式化", "检测到高危自然语言运维意图"),
        (r"`[^`]{1,200}`", "检测到反引号子shell"),
        (r"\$\([^)]{1,200}\)", "检测到命令替换 $(...)"),
    ]

    # 提示词注入模式
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
        """检查用户输入是否包含高危内容。

        Returns:
            {"risk_level": "low"|"high", "reasons": [...], "is_analysis_context": bool}
        """
        high_risk = self._detect(self.HIGH_RISK_PATTERNS, user_input)
        injection = self._detect(self.PROMPT_INJECTION_PATTERNS, user_input)
        is_analysis = self._is_analysis_context(user_input)

        # 如果语境是"分析不可信数据"，高危文本作为数据不进入执行路径
        if is_analysis and (high_risk or injection):
            return {
                "risk_level": "high",
                "reasons": ["输入包含高危文本，但语境是分析不可信数据"],
                "is_analysis_context": True,
            }

        reasons = high_risk + injection
        return {
            "risk_level": "high" if reasons else "low",
            "reasons": reasons or ["未命中高危规则"],
            "is_analysis_context": False,
        }

    def check_plan(self, plan: list[dict], allowed_tool_names: set[str]) -> dict[str, Any]:
        """检查 LLM 生成的计划是否只包含白名单工具。"""
        for step in plan:
            tool = step.get("tool", "")
            if tool not in allowed_tool_names:
                return {
                    "risk_level": "high",
                    "reasons": [f"计划包含未授权工具：{tool}"],
                }
        return {"risk_level": "low", "reasons": ["计划工具均在白名单内"]}

    def scan_untrusted_output(self, tool_call: dict) -> dict[str, Any]:
        """扫描工具输出中是否包含注入内容。"""
        import json
        payload = json.dumps(tool_call.get("result", {}), ensure_ascii=False)
        reasons = self._detect(self.PROMPT_INJECTION_PATTERNS, payload)
        return {
            "detected": bool(reasons),
            "risk_level": "high" if reasons else "low",
            "source_tool": tool_call.get("tool_name"),
            "reasons": reasons,
        }

    # ------------------------------------------------------------------
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
        """NFKC 归一化 + 移除零宽字符。"""
        text = unicodedata.normalize("NFKC", text)
        zero_width = "​-‍﻿"
        return text.translate({ord(c): None for c in zero_width})
