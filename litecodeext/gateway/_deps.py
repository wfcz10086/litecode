"""gateway/_deps.py — 网关路由共享依赖 (lazy import + _auth).

路由 handler 里的所有 module-level state (MODEL_ID / BACKEND_URL / _sessions ...)
都通过 `_srv()` lazy 拿 litecode_server 模块引用, 避免循环 import + 保证 model_switch
热更新后所有 handler 拿到最新值.
"""
from __future__ import annotations

from fastapi import HTTPException, Request


def _srv():
    """Lazy import litecode_server, 每次调用都拿最新模块 (支持 model_switch 热更)."""
    import litecode_server  # type: ignore
    return litecode_server


def _auth(request: Request) -> bool:
    """Bearer 鉴权; 走 litecode_server.TOKEN (启动时固化)."""
    auth = request.headers.get("Authorization", "")
    token = _srv().TOKEN
    return auth.startswith("Bearer ") and auth[7:].strip() == token


def require_auth(request: Request) -> None:
    """未授权直接 401."""
    if not _auth(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
