"""gateway/memory.py — L1/L2 memory endpoints (/v1/memory/*)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from lib.config import HAS_MEMORY, mem_get as _mem_get
from lib.session import get_history as _get_history

from ._deps import _srv, require_auth

router = APIRouter()


def _mem_kwargs(sid: str):
    srv = _srv()
    return dict(
        workspace=srv.WORKSPACE, session_id=sid, vllm_url=srv.BACKEND_URL,
        model_id=srv.MODEL_ID, api_key=srv.API_KEY,
        context_window=srv.CONTEXT_WINDOW,
    )


@router.get("/v1/memory/{sid}")
@router.get("/memory/{sid}")
async def get_memory_api(sid: str, request: Request):
    require_auth(request)
    if not HAS_MEMORY:
        raise HTTPException(503, "memory module not available")
    mgr = _mem_get(**_mem_kwargs(sid))
    hist = _get_history(sid)
    return mgr.get_structure(messages=hist)


@router.post("/v1/memory/{sid}/compress")
@router.post("/memory/{sid}/compress")
async def compress_memory_api(sid: str, request: Request):
    require_auth(request)
    if not HAS_MEMORY:
        raise HTTPException(503, "memory module not available")
    hist = _get_history(sid)
    if not hist:
        return {"ok": False, "reason": "no history"}
    mgr = _mem_get(**_mem_kwargs(sid))
    mgr.check_and_compact(hist, force=True)
    return {"ok": True}


@router.delete("/v1/memory/{sid}")
@router.delete("/memory/{sid}")
async def clear_memory_api(sid: str, request: Request):
    require_auth(request)
    mgr = _mem_get(**_mem_kwargs(sid))
    if mgr.l1_file.exists():
        mgr.l1_file.unlink()
    mgr._init_l1()
    return {"ok": True}


@router.patch("/v1/memory/{sid}/l1")
@router.patch("/memory/{sid}/l1")
async def edit_l1_api(sid: str, request: Request):
    require_auth(request)
    body = await request.json()
    mgr = _mem_get(**_mem_kwargs(sid))
    mgr.l1_file.write_text(body.get("content", ""))
    return {"ok": True}
