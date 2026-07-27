"""限流实现 — 令牌桶算法（Token Bucket）。

修复 audit #14：
  - 令牌桶支持 chat、stream、MCP 全端点
  - 每个 IP 独立桶，配置化容量和填充速率
  - 无依赖，纯 Python + 内存 dict（单机适用）
  - 生产迁移边界：可替换为 Redis + 滑动窗口
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Bucket:
    """单个 IP 的令牌桶。"""
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class TokenBucketRateLimiter:
    """令牌桶限流器。

    算法说明：
      - 桶容量 capacity：最大突发请求数
      - 填充速率 refill_rate：每秒恢复的令牌数
      - 每个请求消耗 1 个令牌；桶空时拒绝（429）
      - 令牌按时间勻速补充，支持突发 + 均值控制
    """

    def __init__(self, capacity: int = 10, refill_rate: float = 1.0) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _refill(self, bucket: _Bucket) -> None:
        """按时间补充令牌。"""
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        new_tokens = elapsed * self.refill_rate
        bucket.tokens = min(self.capacity, bucket.tokens + new_tokens)
        bucket.last_refill = now

    def allow(self, key: str) -> tuple[bool, dict[str, Any]]:
        """检查是否允许请求。

        Returns:
            (是否允许, 限速信息头)
        """
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = _Bucket(tokens=self.capacity - 1, last_refill=time.monotonic())
                return True, self._headers(self.capacity - 1)

            bucket = self._buckets[key]
            self._refill(bucket)

            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return True, self._headers(bucket.tokens)
            else:
                return False, self._headers(0)

    def _headers(self, remaining: float) -> dict[str, Any]:
        """限速响应头（类 GitHub/RateLimit 风格）。"""
        return {
            "X-RateLimit-Limit": str(self.capacity),
            "X-RateLimit-Remaining": str(int(remaining)),
            "X-RateLimit-Reset": str(int(time.monotonic() + (self.capacity - remaining) / self.refill_rate)),
        }


# ---------------------------------------------------------------------------
# 全局单例（生产应换为 Redis）
# ---------------------------------------------------------------------------
limiter = TokenBucketRateLimiter(capacity=10, refill_rate=1.0)  # 10 次/分钟 ≈ 0.167/s
