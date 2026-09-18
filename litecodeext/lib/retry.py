"""lib/retry.py — 统一指数退避 + jitter (#68 收敛).

单一职责: 计算重试等待秒数, 提供 async sleep 便捷函数.
不做函数级重试装饰器 — 调用侧 for-loop 已存在, 只统一延迟策略即可.

policy:
  delay(i) = min(cap, base * factor**i)  * (1 + rand[-jitter, +jitter])
  attempt 从 0 开始; 首次失败传 attempt=0 → base 秒 (+抖动).

默认: base=1.0s, factor=2.0, jitter=0.25 (±25%), cap=30s.
0..3 次典型延迟: ~1s, ~2s, ~4s, ~8s.
"""
from __future__ import annotations
import asyncio
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class BackoffPolicy:
    base: float = 1.0
    factor: float = 2.0
    jitter: float = 0.25
    cap: float = 30.0


DEFAULT_POLICY = BackoffPolicy()
# LLM provider (429/limit) 常用: 更耐心一点, jitter 大一点避羊群
LLM_POLICY = BackoffPolicy(base=1.0, factor=2.0, jitter=0.3, cap=20.0)
# HTTP tool 类调用: 稍快
HTTP_POLICY = BackoffPolicy(base=0.5, factor=2.0, jitter=0.2, cap=10.0)


def next_delay(attempt: int, policy: BackoffPolicy = DEFAULT_POLICY) -> float:
    """返回第 attempt 次失败后应该等待的秒数. attempt 从 0 起."""
    a = max(0, int(attempt))
    raw = policy.base * (policy.factor ** a)
    raw = min(raw, policy.cap)
    if policy.jitter > 0:
        # 对称 jitter: (1 - j) ~ (1 + j)
        j = policy.jitter
        raw *= 1.0 + random.uniform(-j, j)
    return max(0.0, raw)


async def sleep_backoff(attempt: int, policy: BackoffPolicy = DEFAULT_POLICY) -> float:
    """睡眠指数退避 + jitter, 返回实际睡了多久 (便于测试)."""
    d = next_delay(attempt, policy)
    await asyncio.sleep(d)
    return d
