"""plugins/auth/core.py — 集中鉴权真实实现 (S1-G).

从 routers/auth_router.py 搬入; 后者变成 re-export shim.
状态 (_AUTH_ENABLED / _valid_tokens / …) 作为模块级单例, init() 时被填充.
"""
from __future__ import annotations
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response


router = APIRouter()

# ── 状态 (init 时填充) ──────────────────────────────────────
_AUTH_ENABLED: bool = False
_AUTH_PASSWORD: str = ""
_SESSION_DAYS: int = 7
_COOKIE_NAME: str = "lc_auth"
_TOKENS_FILE: Path = Path.home() / ".litecode" / "web_auth_tokens.json"
_valid_tokens: dict = {}

# ── [Q099 UI-Sprint-D] 登录限流: 60s 内 5 次失败 → 锁 60s ─────
_RATE_MAX_FAILS: int = 5
_RATE_WINDOW: int = 60      # 秒 · 统计窗口
_RATE_LOCK: int = 60        # 秒 · 锁定时长
_login_fails: dict = {}     # {ip: [ts_of_each_recent_fail, ...]}
_login_lock_until: dict = {}  # {ip: ts_lock_expires}


def _rate_check(ip: str):
    """抛 HTTPException(429) 如果 IP 处于锁定或已达阈值."""
    now = time.time()
    lock_ts = _login_lock_until.get(ip, 0)
    if lock_ts > now:
        raise HTTPException(status_code=429, detail=f"too many failed logins, retry in {int(lock_ts - now)}s")


def _rate_record_fail(ip: str):
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < _RATE_WINDOW]
    fails.append(now)
    _login_fails[ip] = fails
    if len(fails) >= _RATE_MAX_FAILS:
        _login_lock_until[ip] = now + _RATE_LOCK
        _login_fails[ip] = []  # 清计数, 让锁生效


def _rate_clear(ip: str):
    _login_fails.pop(ip, None)
    _login_lock_until.pop(ip, None)


def _load_tokens():
    global _valid_tokens
    try:
        if _TOKENS_FILE.exists():
            data = json.loads(_TOKENS_FILE.read_text())
            now = time.time()
            _valid_tokens = {k: v for k, v in data.items() if v > now}
    except Exception:
        pass


def _save_tokens():
    try:
        _TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKENS_FILE.write_text(json.dumps(_valid_tokens))
    except Exception:
        pass


def _issue_token() -> str:
    tok = secrets.token_hex(32)
    expiry = time.time() + _SESSION_DAYS * 86400
    _valid_tokens[tok] = expiry
    _save_tokens()
    return tok


def _check_token(tok: str) -> bool:
    if not _AUTH_ENABLED:
        return True
    if not tok:
        return False
    expiry = _valid_tokens.get(tok)
    if expiry and expiry > time.time():
        return True
    _valid_tokens.pop(tok, None)
    return False


def _get_cookie(request: Request) -> str:
    return request.cookies.get(_COOKIE_NAME, "")


def require_auth(request: Request):
    """各 router / web_ui.py 老 endpoint 调用. 未授权抛 401."""
    if _AUTH_ENABLED and not _check_token(_get_cookie(request)):
        raise HTTPException(status_code=401, detail="Unauthorized")


def init(cfg: dict):
    """web_ui.py 启动时调用, 注入 auth 配置."""
    global _AUTH_ENABLED, _AUTH_PASSWORD, _SESSION_DAYS
    a = (cfg or {}).get("web_ui", {}).get("auth", {})
    _AUTH_ENABLED = a.get("enabled", False)
    _AUTH_PASSWORD = a.get("password", "")
    _SESSION_DAYS = int(a.get("session_days", 7))
    _load_tokens()


# ── Endpoints ──────────────────────────────────────────────
@router.post("/api/login")
async def api_login(request: Request, response: Response):
    if not _AUTH_ENABLED:
        return {"ok": True, "msg": "auth disabled"}
    ip = (request.client.host if request.client else "?") or "?"
    _rate_check(ip)
    b = await request.json()
    pwd = b.get("password", "")
    if not hmac.compare_digest(pwd, _AUTH_PASSWORD):
        _rate_record_fail(ip)
        raise HTTPException(status_code=403, detail="wrong password")
    _rate_clear(ip)
    tok = _issue_token()
    response.set_cookie(
        key=_COOKIE_NAME, value=tok,
        max_age=_SESSION_DAYS * 86400,
        httponly=True, samesite="lax",
    )
    return {"ok": True}


@router.post("/api/auth/heartbeat")
async def api_auth_heartbeat(request: Request, response: Response):
    """[Q100 UI-Sprint-D] 心跳延签 — 前端定时调用, 若 token 仍有效则续期 cookie.
    未启用鉴权时直接 ok."""
    if not _AUTH_ENABLED:
        return {"ok": True, "auth": False}
    tok = _get_cookie(request)
    if not _check_token(tok):
        raise HTTPException(status_code=401, detail="session expired")
    # 续期: 重新写 cookie (max_age 复位)
    _valid_tokens[tok] = time.time() + _SESSION_DAYS * 86400
    _save_tokens()
    response.set_cookie(
        key=_COOKIE_NAME, value=tok,
        max_age=_SESSION_DAYS * 86400,
        httponly=True, samesite="lax",
    )
    return {"ok": True, "auth": True}


@router.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie(_COOKIE_NAME)
    return {"ok": True}


@router.get("/api/auth/status")
async def api_auth_status(request: Request):
    return {"enabled": _AUTH_ENABLED, "ok": _check_token(_get_cookie(request))}
