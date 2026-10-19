"""model_router.py — 多模型管理 + 会话级模型 (GET/PUT /api/sessions/{sid}/model)."""
import re

from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()
ORDER = 20  # 注册顺序 (routers.discover 按此升序 include, 路径重叠时先注册者生效)


@router.get("/api/models")
async def api_model_list(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"active": S.model, "models": []}
    d = await S.aproxy("/v1/model/list")
    return d or {"active": S.model, "models": []}


@router.post("/api/models/switch")
async def api_model_switch(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    body = await request.json()
    d = await S.aproxy("/v1/model/switch", "POST", json=body)
    if d and d.get("ok"):
        # [v1.0] 同步所有模型相关字段, 不仅仅是名称.
        # 旧版只更新 MODEL 导致 backend_url/api_key 不同步 → 401 错误
        S.model = d.get("model", S.model)
        S.ctx_window = d.get("context_window", S.ctx_window)
        # SERVER_URL 不变 (它是 litecode_server 本身的地址, 不是 backend)
    return d or {"ok": False, "error": "server unavailable"}


@router.post("/api/models/test")
async def api_model_test(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    body = await request.json()
    # vLLM 等服务冷启动可能很慢: 网关里单次测试上限 30 秒, 这里多留余量
    d = await S.aproxy("/v1/model/test", "POST", json=body, timeout=40)
    if d and "ok" not in d:   # 代理层错误 (超时 / HTTP 4xx) 统一成测试结果的形状
        d = {"ok": False, "model_id": (body or {}).get("model_id") or (body or {}).get("id") or "",
             "latency_ms": 0, "reply_preview": "", "error": d.get("error") or "server error"}
    return d or {"ok": False, "error": "server unavailable"}


@router.post("/api/models/add")
async def api_model_add(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    body = await request.json()
    d = await S.aproxy("/v1/model/add", "POST", json=body)
    return d or {"ok": False, "error": "server unavailable"}


@router.delete("/api/models/{model_id}")
async def api_model_delete(model_id: str, request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    d = await S.aproxy(f"/v1/model/{model_id}", "DELETE")
    return d or {"ok": False, "error": "server unavailable"}


# ── 会话级模型 [per-chat-model 2026-10-19] ─────────────────────────────
# 只改这个会话下一轮起用的模型, 不动全局默认 (全局切换仍是 /api/models/switch)。
# 存储与解析见 lib/session_model.py; 生效在 gateway/openai_compat.with_turn_model。
_SID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _wui():
    import web_ui  # type: ignore  lazy, 避免循环 import (同 sessions_router)
    return web_ui


def _check_session(sid: str, request: Request) -> None:
    if not _SID_RE.match(sid or ""):
        raise HTTPException(400, "bad session id")
    from lib.owner import require_owner
    require_owner(_wui().load_session(sid), request)


async def _active_model_id() -> str:
    if S.aproxy is not None:
        d = await S.aproxy("/v1/model/list")
        if isinstance(d, dict) and d.get("active"):
            return d["active"]
    return S.model


async def session_model_info(sid: str) -> dict:
    from lib import session_model as _sm
    entry, note, stored = _sm.resolve(sid)
    active = await _active_model_id()
    eff = entry.get("id") if entry else active
    if not entry:
        from lib.config import find_model_entry
        try:
            entry_lab = find_model_entry(active).get("label", "")
        except Exception:
            entry_lab = ""
    else:
        entry_lab = entry.get("label", "")
    return {"sid": sid, "model_id": stored or None, "effective": eff,
            "label": entry_lab or eff,
            "follows_default": not entry, "missing": bool(note), "note": note,
            "default_model": active}


@router.get("/api/sessions/{sid}/model")
async def api_session_model_get(sid: str, request: Request):
    require_auth(request)
    _check_session(sid, request)
    return await session_model_info(sid)


@router.put("/api/sessions/{sid}/model")
async def api_session_model_put(sid: str, request: Request):
    """{model_id: "<config.models 里的 id>"} 指定本会话模型; {model_id: null} 跟随全局默认。
    进行中的轮次不受影响 (本轮开始时已定模型), 下一轮生效。"""
    require_auth(request)
    _check_session(sid, request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    mid = (body or {}).get("model_id") if isinstance(body, dict) else None
    from lib import session_model as _sm
    from lib.config import find_model_entry
    if mid:
        if not isinstance(mid, str) or not find_model_entry(mid):
            raise HTTPException(404, f"model {mid} not found in models list")
    _sm.set_model_id(sid, mid or None)
    try:
        from lib.audit import audit_log
        audit_log(request, "session.model", sid, model_id=mid or "(default)")
    except Exception:
        pass
    return {"ok": True, **(await session_model_info(sid))}
