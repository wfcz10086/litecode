"""lib/owner.py — session owner 隔离 (P0-#14).

单人自用场景, owner 事实上只在两种取值间徘徊:
    - "admin" (启用鉴权, cookie 有效)
    - "anon"  (未启用鉴权)
故 require_owner 允许 admin↔anon 互通, 只在真正跨用户时抛 403.
未来若接入多账号, current_user 换成真实读 token→uid 即可.
"""
from __future__ import annotations
from typing import Optional
from fastapi import HTTPException, Request


def current_user(request: Optional[Request]) -> str:
    if request is None:
        return "anon"
    try:
        from plugins.auth.core import _AUTH_ENABLED, _get_cookie, _check_token
        if not _AUTH_ENABLED:
            return "anon"
        tok = _get_cookie(request)
        return "admin" if _check_token(tok) else "anon"
    except Exception:
        return "anon"


_COMPAT_PAIR = {"admin", "anon"}


def require_owner(session_dict: dict, request: Optional[Request]) -> None:
    """匹配 session.owner 与当前用户. 老 session 无字段则自动 stamp.
    admin/anon 视同 (单人自用). 跨用户抛 403."""
    if not isinstance(session_dict, dict):
        return
    owner = session_dict.get("owner")
    me = current_user(request)
    if not owner:
        session_dict["owner"] = me
        return
    if owner == me:
        return
    if owner in _COMPAT_PAIR and me in _COMPAT_PAIR:
        return
    raise HTTPException(
        status_code=403,
        detail=f"forbidden: session owner={owner}, caller={me}",
    )


# ── 归属校验小缓存 [owner-cache 2026-10-18] ─────────────────────────────
# 高频轮询接口 (子代理面板每 3s) 每次 load_session 全量读盘解析太重。只缓存"通过"的结果:
# key=(当前用户, sid), 值=(会话存储指纹, 过期时刻)。指纹由调用方给 (会话文件/墓碑文件的 mtime),
# 会话被改写/删除/转移 → 指纹变 → 失效重查; 否则最多 TTL 秒后重查。拒绝 (403) 从不缓存, 每次真查。
import threading as _threading
import time as _time

OWNER_CACHE_TTL = 60.0
_OWNER_CACHE_MAX = 512
_owner_cache: dict = {}
_owner_cache_lock = _threading.Lock()


def clear_owner_cache() -> None:
    with _owner_cache_lock:
        _owner_cache.clear()


def require_owner_cached(sid: str, request: Optional[Request], load_fn, stamp_fn=None,
                         ttl: float = OWNER_CACHE_TTL) -> None:
    """同 require_owner(load_fn(sid), request), 但通过的结果按 (用户, sid) 缓存 ttl 秒。

    stamp_fn(sid) 返回会话存储指纹 (可比较); 与缓存时不同即失效。stamp_fn 抛错 → 本次不走缓存。"""
    me = current_user(request)
    key = (me, sid)
    try:
        stamp = stamp_fn(sid) if stamp_fn else None
        cacheable = True
    except Exception:
        stamp, cacheable = None, False
    now = _time.monotonic()
    if cacheable:
        with _owner_cache_lock:
            hit = _owner_cache.get(key)
        if hit is not None and hit[0] == stamp and hit[1] > now:
            return
    require_owner(load_fn(sid), request)   # 越权在这里抛 403, 不进缓存
    if not cacheable:
        return
    with _owner_cache_lock:
        if len(_owner_cache) >= _OWNER_CACHE_MAX:
            stale = [k for k, v in _owner_cache.items() if v[1] <= now]
            for k in stale or list(_owner_cache)[: _OWNER_CACHE_MAX // 4]:
                _owner_cache.pop(k, None)
        _owner_cache[key] = (stamp, now + ttl)
