"""预算/熔断配置 — Phase F：长任务防失控。

包含：
  - 最大节点步数（防止无限循环）
  - 最大工具调用数（防止费用失控）
  - 最大运行时长（秒）
  - 连续无进展检测（相同签名出现 N 次触发熔断）
  - kill switch（配置开关，可紧急停止所有 run）
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_v4.settings import Settings

# ---------------------------------------------------------------------------
# 预算阈值（默认值；运行时优先使用注入的 Settings）
# ---------------------------------------------------------------------------
DEFAULT_MAX_STEPS = 10
DEFAULT_MAX_TOOL_CALLS = 8
DEFAULT_MAX_DURATION_SEC = 60
DEFAULT_MAX_SAME_PLAN = 2


class BudgetConfig:
    """预算配置（运行时只读）。测试可注入自定义值。"""

    def __init__(self, settings=None) -> None:
        from app_v4.settings import Settings
        s = settings or Settings()
        self.max_steps: int = s.max_steps
        self.max_tool_calls: int = s.max_tool_calls
        self.max_duration_sec: int = s.max_duration_sec
        self.max_same_plan: int = s.max_same_plan
        self.kill_switch: bool = s.kill_switch

    @staticmethod
    def check_kill_switch() -> bool:
        """检查 kill switch 是否激活（每次读取以支持热更新）。"""
        return os.getenv("APP_V4_KILL_SWITCH", "").lower() in ("1", "true", "yes")


def budget_exceeded(
    step_count: int,
    tool_call_count: int,
    duration_sec: float,
    settings=None,
) -> tuple[bool, str]:
    """检查是否超出预算。

    Returns:
        (是否超出, 原因描述)
    """
    from app_v4.settings import Settings
    s = settings or Settings()
    max_steps = s.max_steps
    max_tool_calls = s.max_tool_calls
    max_dur = s.max_duration_sec

    if step_count > max_steps:
        return True, f"超出最大步数 {max_steps}（当前 {step_count}）"
    if tool_call_count > max_tool_calls:
        return True, f"超出最大工具调用数 {max_tool_calls}（当前 {tool_call_count}）"
    if duration_sec > max_dur:
        return True, f"超出最大运行时长 {max_dur}s（当前 {duration_sec:.1f}s）"
    return False, ""
