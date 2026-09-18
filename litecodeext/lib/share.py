"""lib/share.py — session 分享 token 存储 (P0-#16).

存 ~/.litecode/share_tokens.json:
    { "<token>": {"sid": ..., "created": ts, "expires": ts,
                  "read_only": true, "created_by": "admin"} }
访问 GET /api/share/{token} 时按 token 查 sid, 校验未过期, 返脱敏 snapshot.
"""
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path
from typing import Optional

_STORE_DIR = Path.home() / ".litecode"
_STORE_FILE = _STORE_DIR / "share_tokens.json"

_DEFAULT_TTL_SEC = 7 * 86400  # 7 天


def _load() -> dict:
    try:
        if _STORE_FILE.exists():
            return json.loads(_STORE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save(store: dict) -> None:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2))
    tmp.replace(_STORE_FILE)


def _purge_expired(store: dict) -> dict:
    now = time.time()
    return {k: v for k, v in store.items() if v.get("expires", 0) > now}


def create_share(sid: str, created_by: str = "anon",
                 ttl_sec: int = _DEFAULT_TTL_SEC,
                 read_only: bool = True) -> dict:
    tok = secrets.token_urlsafe(24)
    now = time.time()
    rec = {
        "sid": sid,
        "created": now,
        "expires": now + max(60, int(ttl_sec)),
        "read_only": bool(read_only),
        "created_by": created_by,
    }
    store = _purge_expired(_load())
    store[tok] = rec
    _save(store)
    return {"token": tok, **rec}


def lookup_share(token: str) -> Optional[dict]:
    if not token:
        return None
    store = _load()
    rec = store.get(token)
    if not rec:
        return None
    if rec.get("expires", 0) <= time.time():
        # 过期即删
        store.pop(token, None)
        _save(store)
        return None
    return rec


def revoke_share(token: str) -> bool:
    store = _load()
    if token in store:
        store.pop(token)
        _save(store)
        return True
    return False


def list_shares(sid: Optional[str] = None) -> list:
    store = _purge_expired(_load())
    _save(store)
    out = []
    for tok, rec in store.items():
        if sid and rec.get("sid") != sid:
            continue
        out.append({"token": tok, **rec})
    return out
