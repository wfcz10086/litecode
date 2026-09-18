"""lib/rate_limit.py — 极简 per-IP 滑窗节流.

进程内 dict, 无外部依赖. 用法::

    from lib.rate_limit import rate_check
    @app.post(...)
    async def x(request: Request, ...):
        rate_check(request, "chat", 30, 60)   # 30 次 / 60 秒
        ...

超限抛 HTTPException(429). 每 bucket 独立计数, 老时间戳自动清理.
"""
from __future__ import annotations
import time
from typing import Dict, List, Tuple
from fastapi import HTTPException, Request

_BUCKETS: Dict[Tuple[str, str], List[float]] = {}


def _client_ip(request: Request) -> str:
    # X-Forwarded-For 走反代时优先, 否则用直连
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return (request.client.host if request.client else "?") or "?"


def rate_check(request: Request, bucket: str, limit: int, window_sec: int) -> None:
    """滑窗节流. 触发抛 429."""
    if limit <= 0 or window_sec <= 0:
        return
    ip = _client_ip(request)
    key = (bucket, ip)
    now = time.time()
    # GC + append 是同一 dict 上的 O(n), n 是窗口内请求数, 单人自用足够
    stamps = [t for t in _BUCKETS.get(key, []) if now - t < window_sec]
    if len(stamps) >= limit:
        oldest = stamps[0]
        retry_after = int(window_sec - (now - oldest)) + 1
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded ({bucket}: {limit}/{window_sec}s), "
                   f"retry in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )
    stamps.append(now)
    _BUCKETS[key] = stamps


def rate_reset(bucket: str = "", ip: str = "") -> None:
    """测试/后台用. 空参 = 清全部."""
    if not bucket and not ip:
        _BUCKETS.clear()
        return
    to_del = [k for k in _BUCKETS
              if (not bucket or k[0] == bucket) and (not ip or k[1] == ip)]
    for k in to_del:
        del _BUCKETS[k]
