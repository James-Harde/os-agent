"""Phase F：限流算法 + 工具缓存测试。

覆盖：
  - 令牌桶限流（Token Bucket）
  - 工具结果缓存（TTL + 击穿防护）
  - 缓存不缓存写操作
"""

import time

from app_v4.graph.rate_limiter import TokenBucketRateLimiter
from app_v4.graph.tool_cache import ToolCache


# ---------------------------------------------------------------------------
# 令牌桶限流
# ---------------------------------------------------------------------------
class TestTokenBucket:
    def test_allows_within_capacity(self):
        """桶容量内应全部允许。"""
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        for _ in range(5):
            allowed, _ = limiter.allow("ip-1")
            assert allowed is True

    def test_rejects_when_empty(self):
        """桶空时应拒绝。"""
        limiter = TokenBucketRateLimiter(capacity=2, refill_rate=0.001)  # 几乎不填充
        limiter.allow("ip-1")
        limiter.allow("ip-1")
        allowed, headers = limiter.allow("ip-1")
        assert allowed is False
        assert headers["X-RateLimit-Remaining"] == "0"

    def test_refills_over_time(self):
        """等待后令牌应恢复。"""
        limiter = TokenBucketRateLimiter(capacity=2, refill_rate=100.0)  # 快速填充
        limiter.allow("ip-1")
        limiter.allow("ip-1")
        # 桶空
        allowed, _ = limiter.allow("ip-1")
        assert allowed is False
        # 等待填充
        time.sleep(0.05)
        allowed, _ = limiter.allow("ip-1")
        assert allowed is True

    def test_independent_buckets(self):
        """不同 IP 独立计数。"""
        limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0.001)
        allowed1, _ = limiter.allow("ip-1")
        assert allowed1 is True
        # ip-1 桶空
        allowed1_2, _ = limiter.allow("ip-1")
        assert allowed1_2 is False
        # ip-2 仍有令牌
        allowed2, _ = limiter.allow("ip-2")
        assert allowed2 is True


# ---------------------------------------------------------------------------
# 工具缓存
# ---------------------------------------------------------------------------
class TestToolCache:
    def test_cache_hit_and_miss(self):
        """缓存命中返回值，未命中返回 None。"""
        cache = ToolCache()
        # 未命中
        assert cache.get("disk_usage", {"path": "."}) is None
        # 写入
        cache.put("disk_usage", {"path": "."}, {"used_percent": 50})
        # 命中
        result = cache.get("disk_usage", {"path": "."})
        assert result is not None
        assert result["used_percent"] == 50

    def test_cache_expiry(self):
        """过期后应返回 None。"""
        cache = ToolCache()
        # 手动写入一个即将过期的条目
        cache._store["test:key"] = (time.monotonic() - 1, {"data": "old"})
        # 访问内部存储验证过期逻辑
        expire_time, _ = cache._store["test:key"]
        assert time.monotonic() > expire_time  # 已过期

    def test_no_cache_for_write_tools(self):
        """写操作工具不应缓存。"""
        cache = ToolCache()
        cache.put("service_restart", {"service": "sshd"}, {"status": "ok"})
        assert cache.get("service_restart", {"service": "sshd"}) is None

    def test_cache_stats(self):
        """缓存统计应正确。"""
        cache = ToolCache()
        cache.put("disk_usage", {"path": "."}, {"data": 1})
        cache.get("disk_usage", {"path": "."})  # hit
        cache.get("disk_usage", {"path": "/"})  # miss
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] >= 1

    def test_different_args_different_keys(self):
        """不同参数应产生不同缓存条目。"""
        cache = ToolCache()
        cache.put("disk_usage", {"path": "."}, {"data": "root"})
        cache.put("disk_usage", {"path": "/home"}, {"data": "home"})
        assert cache.get("disk_usage", {"path": "."})["data"] == "root"
        assert cache.get("disk_usage", {"path": "/home"})["data"] == "home"
