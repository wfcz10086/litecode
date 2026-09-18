"""#68 单元测试: lib/retry.py 指数退避 + jitter 收敛."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/opt/litecode/litecodeext")))

import asyncio
import statistics
import time

import pytest

from lib.retry import (
    BackoffPolicy,
    DEFAULT_POLICY,
    LLM_POLICY,
    HTTP_POLICY,
    next_delay,
    sleep_backoff,
)


def test_next_delay_no_jitter_grows_exp():
    p = BackoffPolicy(base=1.0, factor=2.0, jitter=0.0, cap=1000.0)
    assert next_delay(0, p) == 1.0
    assert next_delay(1, p) == 2.0
    assert next_delay(2, p) == 4.0
    assert next_delay(3, p) == 8.0
    assert next_delay(4, p) == 16.0


def test_next_delay_respects_cap():
    p = BackoffPolicy(base=1.0, factor=2.0, jitter=0.0, cap=5.0)
    assert next_delay(0, p) == 1.0
    assert next_delay(1, p) == 2.0
    assert next_delay(2, p) == 4.0
    assert next_delay(3, p) == 5.0  # capped
    assert next_delay(10, p) == 5.0  # still capped


def test_jitter_within_pct_bounds():
    p = BackoffPolicy(base=1.0, factor=2.0, jitter=0.25, cap=1000.0)
    # 100 次 attempt=2 (raw=4s) 应该落在 [3, 5] 内 (±25%)
    samples = [next_delay(2, p) for _ in range(200)]
    assert min(samples) >= 4.0 * 0.75 - 1e-9
    assert max(samples) <= 4.0 * 1.25 + 1e-9
    # 平均应该接近 raw (~4.0), stdev > 0 说明真的抖了
    assert 3.7 < statistics.mean(samples) < 4.3
    assert statistics.stdev(samples) > 0.1


def test_negative_attempt_treated_as_zero():
    p = BackoffPolicy(base=1.0, factor=2.0, jitter=0.0, cap=100.0)
    assert next_delay(-3, p) == 1.0


def test_policy_presets_sane():
    assert LLM_POLICY.base == 1.0 and LLM_POLICY.factor == 2.0
    assert HTTP_POLICY.base < LLM_POLICY.base   # HTTP 更快
    assert DEFAULT_POLICY.jitter > 0            # 默认有抖动


def test_sleep_backoff_actually_sleeps_and_returns_delay():
    async def _run():
        # 用小 base 加快测试, 关 jitter 拿准值
        p = BackoffPolicy(base=0.05, factor=2.0, jitter=0.0, cap=1.0)
        t0 = time.monotonic()
        actual = await sleep_backoff(2, p)  # 0.05 * 4 = 0.2s
        elapsed = time.monotonic() - t0
        return actual, elapsed

    actual, elapsed = asyncio.run(_run())
    assert 0.19 <= actual <= 0.21
    # 事件循环调度会略久 (~10ms 抖动), 但不该 >100ms 差距
    assert elapsed >= actual - 0.01
    assert elapsed <= actual + 0.15


def test_jitter_never_negative_sleep():
    p = BackoffPolicy(base=0.01, factor=2.0, jitter=0.9, cap=1.0)
    # 极端 jitter 也不能返回负数
    for i in range(50):
        assert next_delay(i, p) >= 0.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
