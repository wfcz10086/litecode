"""plugins/auth/core.py — 集中鉴权真实实现 (S1-G).

从 routers/auth_router.py 搬入; 后者变成 re-export shim.
状态 (_AUTH_ENABLED / _valid_tokens / …) 作为模块级单例, init() 时被填充.
"""
from __future__ import annotations
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response


router = APIRouter()

# ── 状态 (init 时填充) ──────────────────────────────────────
_AUTH_ENABLED: bool = False
_AUTH_PASSWORD: str = ""
_SESSION_DAYS: int = 7
_COOKIE_NAME: str = "lc_auth"
_TOKENS_FILE: Path = Path.home() / ".litecode" / "web_auth_tokens.json"
_valid_tokens: dict = {}
_CFG_PATH: Optional[Path] = None   # 密码所在 config.json; None → 用 routers.state.S.cfg_path
_CFG_WRITE_LOCK = threading.Lock()

# 修改密码的校验规则
_PW_MIN_LEN: int = 8
_PW_MAX_LEN: int = 256
_PW_PLACEHOLDERS = {"change_me", "changeme"}   # 公开包 release_scrub_config 写入的占位符

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


def init(cfg: dict, cfg_path=None):
    """web_ui.py 启动时调用, 注入 auth 配置.
    cfg_path: 密码所在的 config.json (修改密码时写回); 不传则运行时取 routers.state.S.cfg_path."""
    global _AUTH_ENABLED, _AUTH_PASSWORD, _SESSION_DAYS, _CFG_PATH
    a = (cfg or {}).get("web_ui", {}).get("auth", {})
    _AUTH_ENABLED = a.get("enabled", False)
    _AUTH_PASSWORD = a.get("password", "")
    _SESSION_DAYS = int(a.get("session_days", 7))
    if cfg_path is not None:
        _CFG_PATH = Path(cfg_path)
    _load_tokens()


def _pw_eq(a, b) -> bool:
    """常量时间比较; 按 utf-8 字节比, 避免 compare_digest 对非 ASCII str 抛 TypeError."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "?") or "?"


def _resolve_cfg_path() -> Path:
    if _CFG_PATH is not None:
        return _CFG_PATH
    from routers.state import S  # 延迟导入: 避免 plugins ↔ routers 循环
    return Path(S.cfg_path)


def _detect_indent(text: str):
    """沿用文件现有缩进风格 (config.json 目前是 4 空格)."""
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and line != stripped:
            ws = line[: len(line) - len(stripped)]
            return "\t" if ws.startswith("\t") else len(ws)
    return 4


def _write_config_password(path: Path, new_password: str) -> None:
    """把 web_ui.auth.password 写回 config.json, 其余内容原样保留.

    不用 os.replace 的 tmp+rename: config.json 在容器里是单文件 bind-mount,
    rename 会换 inode 让宿主/容器两边失联 (容器内直接 EBUSY). 所以和 config_router
    一样原地写: 先在内存里完整序列化并回读校验, 再 truncate+write+fsync, 尽量缩短半写窗口.
    """
    with _CFG_WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        cfg = json.loads(raw)
        if not isinstance(cfg, dict):
            raise ValueError("config root is not an object")
        web_ui = cfg.setdefault("web_ui", {})
        auth = web_ui.setdefault("auth", {})
        auth["password"] = new_password
        out = json.dumps(cfg, ensure_ascii=False, indent=_detect_indent(raw))
        if raw.endswith("\n"):
            out += "\n"
        if json.loads(out) != cfg:   # 序列化自检, 不对就不落盘
            raise ValueError("config serialization mismatch")
        data = out.encode("utf-8")
        fd = os.open(str(path), os.O_WRONLY)
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)


def _validate_new_password(old: str, new) -> Optional[str]:
    """返回错误说明; None = 合格."""
    if not isinstance(new, str) or not new:
        return "新密码不能为空"
    if len(new) < _PW_MIN_LEN:
        return f"新密码至少 {_PW_MIN_LEN} 个字符"
    if len(new) > _PW_MAX_LEN:
        return f"新密码最多 {_PW_MAX_LEN} 个字符"
    if new != new.strip():
        return "新密码首尾不能有空白"
    if new.strip().lower() in _PW_PLACEHOLDERS:
        return "不能使用占位符 CHANGE_ME 作为密码"
    if _pw_eq(new, old):
        return "新密码不能与当前密码相同"
    return None


# ── Endpoints ──────────────────────────────────────────────
@router.post("/api/login")
async def api_login(request: Request, response: Response):
    if not _AUTH_ENABLED:
        return {"ok": True, "msg": "auth disabled"}
    ip = (request.client.host if request.client else "?") or "?"
    _rate_check(ip)
    b = await request.json()
    pwd = b.get("password", "") if isinstance(b, dict) else ""
    if not _pw_eq(pwd, _AUTH_PASSWORD):
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
async def api_logout(request: Request, response: Response):
    # 服务端同步吊销 token: 只删浏览器 cookie 的话, 旧 token 在有效期内仍可直接调用 API
    tok = _get_cookie(request)
    if tok and _valid_tokens.pop(tok, None) is not None:
        _save_tokens()
    response.delete_cookie(_COOKIE_NAME)
    return {"ok": True}


@router.get("/api/auth/status")
async def api_auth_status(request: Request):
    return {"enabled": _AUTH_ENABLED, "ok": _check_token(_get_cookie(request))}


@router.post("/api/auth/password")
async def api_auth_password(request: Request):
    """修改 Web 登录密码: 校验旧密码 → 写回 config.json → 内存立即生效 → 吊销其它会话.

    请求体 {old_password, new_password}. 旧密码错误计入登录限流 (同一 IP 共用计数).
    当前浏览器的 token 保留, 其它设备/CLI 的 token 全部作废.
    """
    global _AUTH_PASSWORD
    require_auth(request)
    ip = _client_ip(request)
    _rate_check(ip)
    try:
        b = await request.json()
    except Exception:
        b = None
    if not isinstance(b, dict):
        raise HTTPException(status_code=400, detail="body must be object")
    old = b.get("old_password", "")
    new = b.get("new_password", "")
    if not _pw_eq(old, _AUTH_PASSWORD):
        _rate_record_fail(ip)
        try:
            from lib.audit import audit_log
            audit_log(request, "auth.password_change_failed", "web_ui", reason="wrong_old_password")
        except Exception:
            pass
        raise HTTPException(status_code=403, detail="当前密码不正确")
    err = _validate_new_password(_AUTH_PASSWORD, new)
    if err:
        raise HTTPException(status_code=400, detail=err)

    try:
        _write_config_password(_resolve_cfg_path(), new)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置失败: {type(e).__name__}")
    _AUTH_PASSWORD = new          # /api/login 立即用新密码, 无需重启
    _rate_clear(ip)

    cur = _get_cookie(request)
    keep = {cur: _valid_tokens[cur]} if cur and cur in _valid_tokens else {}
    revoked = len(_valid_tokens) - len(keep)
    _valid_tokens.clear()
    _valid_tokens.update(keep)
    _save_tokens()
    try:
        from lib.audit import audit_log
        audit_log(request, "auth.password_change", "web_ui", revoked_sessions=revoked)
    except Exception:
        pass
    return {"ok": True, "revoked": revoked,
            "msg": "密码已修改, 其它设备的登录已失效"}
