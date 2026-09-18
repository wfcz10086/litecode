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
