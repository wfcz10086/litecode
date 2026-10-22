"""memory_router.py — 记忆 / artifacts."""
import json
from pathlib import Path

from fastapi import APIRouter, Request

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()
ORDER = 50  # 注册顺序 (routers.discover 按此升序 include, 路径重叠时先注册者生效)


@router.get("/api/artifacts/{sid}")
async def api_artifacts(sid: str, request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"sid": sid, "artifacts": [], "count": 0}
    d = await S.aproxy(f"/v1/sessions/{sid}/artifacts")
    return d or {"sid": sid, "artifacts": [], "count": 0}


@router.post("/api/memory/{sid}/compress")
async def api_compress(sid: str, request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "reason": "server unavailable"}
    d = await S.aproxy(f"/v1/memory/{sid}/compress", "POST")
    return d or {"ok": False, "reason": "server unavailable"}


@router.delete("/api/memory/{sid}")
async def api_mem_clear(sid: str, request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False}
    d = await S.aproxy(f"/v1/memory/{sid}", "DELETE")
    return d or {"ok": False}


@router.patch("/api/memory/{sid}/l1")
async def api_edit_l1(sid: str, request: Request):
    require_auth(request)
    b = await request.json()
    if S.aproxy is not None:
        d = await S.aproxy(f"/v1/memory/{sid}/l1", "PATCH", json=b)
        if d:
            return d
    # 回退: 直接写盘 (跟原 web_ui.py 行为一致)
    _paths = S.cfg.get("paths", {})
    _agt = S.cfg.get("agent", {})
    ws = Path(_paths.get("workspace_base", _agt.get("workspace", "/tmp/litecode_workspace")))
    sess_base = Path(_paths.get("sessions_dir", str(ws / "sessions")))
    l1 = sess_base / sid / "MEMORY.md"
    l1.parent.mkdir(parents=True, exist_ok=True)
    l1.write_text(b.get("content", ""))
    return {"ok": True}


# ── 记忆面板: 全局用户档案 / 会话条目 / 跨会话索引 (转发到 gateway/memory.py) ──
from urllib.parse import quote as _q  # noqa: E402


def _qs(request: Request, *keys: str) -> str:
    pairs = [(k, request.query_params.get(k)) for k in keys if request.query_params.get(k)]
    return ("?" + "&".join(f"{k}={_q(str(v))}" for k, v in pairs)) if pairs else ""


async def _fwd(path: str, method: str = "GET", **kw):
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    return await S.aproxy(path, method, **kw) or {"ok": False, "error": "server unavailable"}


@router.get("/api/profile")
async def api_profile(request: Request):
    require_auth(request)
    return await _fwd("/v1/profile" + _qs(request, "sid"))


@router.post("/api/profile/facts")
async def api_profile_add(request: Request):
    require_auth(request)
    return await _fwd("/v1/profile/facts" + _qs(request, "sid"), "POST", json=await request.json())


@router.patch("/api/profile/facts/{fid}")
async def api_profile_edit(fid: str, request: Request):
    require_auth(request)
    return await _fwd(f"/v1/profile/facts/{_q(fid)}" + _qs(request, "sid"), "PATCH", json=await request.json())


@router.delete("/api/profile/facts/{fid}")
async def api_profile_delete(fid: str, request: Request):
    require_auth(request)
    return await _fwd(f"/v1/profile/facts/{_q(fid)}" + _qs(request, "sid"), "DELETE")


@router.put("/api/profile/user_md")
async def api_profile_user_md(request: Request):
    require_auth(request)
    return await _fwd("/v1/profile/user_md" + _qs(request, "sid"), "PUT", json=await request.json())


@router.delete("/api/memory/{sid}/entry")
async def api_mem_entry_delete(sid: str, request: Request):
    require_auth(request)
    return await _fwd(f"/v1/memory/{_q(sid)}/entry", "DELETE", json=await request.json())


@router.get("/api/memory_index")
async def api_memory_index(request: Request):
    require_auth(request)
    return await _fwd("/v1/memory_index" + _qs(request, "q", "category", "limit"))


@router.delete("/api/memory_index/{rid}")
async def api_memory_index_delete(rid: int, request: Request):
    require_auth(request)
    return await _fwd(f"/v1/memory_index/{rid}", "DELETE")
