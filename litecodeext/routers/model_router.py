"""model_router.py — 多模型管理 + 会话级模型 (GET/PUT /api/sessions/{sid}/model)."""
import re

from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth
from routers._body import json_object
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
    body = await json_object(request)
    d = await S.aproxy("/v1/model/switch", "POST", json=body)
    _raise_upstream(d)
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
    body = await json_object(request)
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
    body = await json_object(request)
    d = await S.aproxy("/v1/model/add", "POST", json=body)
    return d or {"ok": False, "error": "server unavailable"}


@router.delete("/api/models/{model_id}")
async def api_model_delete(model_id: str, request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    # [ui-audit 2026-10-21] 上游对不存在的 id 也回 ok 并整份重写 config.json; 这里先查列表, 不存在直接 404
    lst = await S.aproxy("/v1/model/list")
    if isinstance(lst, dict) and isinstance(lst.get("models"), list):
        if not any(isinstance(m, dict) and m.get("id") == model_id for m in lst["models"]):
            raise HTTPException(404, f"模型 {model_id} 不在列表里")
    d = await S.aproxy(f"/v1/model/{model_id}", "DELETE")
    _raise_upstream(d)
    return d or {"ok": False, "error": "server unavailable"}


def _raise_upstream(d) -> None:
    """aproxy 把上游 4xx 包成 200 + {"error": "HTTP 404", "status": 404}; 这里还原成真实状态码。"""
    if isinstance(d, dict) and not d.get("ok") and isinstance(d.get("status"), int) and 400 <= d["status"] < 500:
        raise HTTPException(d["status"], d.get("detail") or d.get("error") or f"HTTP {d['status']}")


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
    from routers.sessions_router import session_exists
    if not session_exists(sid, _wui()):
        raise HTTPException(404, "会话不存在或已删除")
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
    body = await json_object(request)
    if "model_id" not in body:
        raise HTTPException(400, "缺少 model_id (跟随默认请传 null)")
    mid = body.get("model_id")
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


# ── 会话级思考强度 [session-thinking 2026-10-21] ───────────────────────
# 输入框「🧠」只改本会话 (下一轮起生效); 全局默认在设置抽屉里改 (PATCH /api/config)。
def _global_thinking_level() -> str:
    from lib import session_model as _sm
    m = (S.cfg or {}).get("model", {}) or {}
    try:
        from lib.config import load_cfg
        m = load_cfg().get("model", {}) or m
    except Exception:
        pass
    return _sm.level_of(bool(m.get("enable_thinking")), int(m.get("thinking_budget") or 0))


def session_thinking_info(sid: str) -> dict:
    from lib import session_model as _sm
    lv = _sm.get_thinking(sid)
    dflt = _global_thinking_level()
    return {"sid": sid, "level": lv or None, "effective": lv or dflt,
            "follows_default": not lv, "default": dflt, "levels": list(_sm.THINK_LEVELS)}


@router.get("/api/sessions/{sid}/thinking")
async def api_session_thinking_get(sid: str, request: Request):
    require_auth(request)
    _check_session(sid, request)
    return session_thinking_info(sid)


@router.put("/api/sessions/{sid}/thinking")
async def api_session_thinking_put(sid: str, request: Request):
    """{level: "off"|"low"|"mid"|"high"} 指定本会话; {level: null} 跟随全局默认。"""
    require_auth(request)
    _check_session(sid, request)
    body = await json_object(request)
    if "level" not in body:
        raise HTTPException(400, "缺少 level (跟随默认请传 null)")
    lv = body.get("level")
    from lib import session_model as _sm
    if lv is not None and lv not in _sm.THINK_LEVELS:
        raise HTTPException(400, f"level 只能是 {'/'.join(_sm.THINK_LEVELS)} 或 null")
    _sm.set_thinking(sid, lv or None)
    try:
        from lib.audit import audit_log
        audit_log(request, "session.thinking", sid, level=lv or "(default)")
    except Exception:
        pass
    return {"ok": True, **session_thinking_info(sid)}
