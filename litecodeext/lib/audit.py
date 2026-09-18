"""lib/audit.py — 审计日志 JSONL 落盘.

用法::

    from lib.audit import audit_log
    audit_log(request, "session.delete", sid, extra_hint="value")

每次写一行 JSON 到 ~/.litecode/audit.log. 失败静默 (审计不能拖住主流程).
字段: ts / user / ip / action / target / meta.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional
from fastapi import Request

_AUDIT_DIR = Path.home() / ".litecode"
_AUDIT_FILE = _AUDIT_DIR / "audit.log"


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return "?"
    try:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
        return (request.client.host if request.client else "?") or "?"
    except Exception:
        return "?"


def _current_user(request: Optional[Request]) -> str:
    if request is None:
        return "?"
    try:
        from plugins.auth.core import _AUTH_ENABLED, _get_cookie, _check_token
        if not _AUTH_ENABLED:
            return "anon"
        tok = _get_cookie(request)
        return "admin" if _check_token(tok) else "anon"
    except Exception:
        return "?"


def audit_log(request: Optional[Request], action: str, target: str = "", **meta) -> None:
    """Append single JSONL line. Never raise."""
    try:
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.time(),
            "user": _current_user(request),
            "ip": _client_ip(request),
            "action": action,
            "target": target,
        }
        if meta:
            rec["meta"] = meta
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[audit] write fail: {e}")


def audit_path() -> Path:
    return _AUDIT_FILE
