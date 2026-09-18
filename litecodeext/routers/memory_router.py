"""memory_router.py — 记忆 / artifacts."""
import json
from pathlib import Path

from fastapi import APIRouter, Request

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


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
