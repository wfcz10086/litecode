"""timer_router.py — 定时任务 CRUD + 通知 + 历史."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from routers.auth_router import require_auth

router = APIRouter()


# Timer Manager 延迟 import — 避免 timer_manager 模块缺失时 web_ui 起不来
try:
    from timer_manager import get_timer_manager as _timer_manager  # type: ignore
    _TIMER_AVAILABLE = True
except ImportError:
    _TIMER_AVAILABLE = False
    _timer_manager = None  # type: ignore


@router.get("/api/timers")
async def api_timers(request: Request):
    require_auth(request)
    if not _TIMER_AVAILABLE:
        return {"available": False, "timers": []}
    return {"available": True, "timers": _timer_manager().list_timers()}


@router.post("/api/timers")
async def api_timer_add(request: Request):
    require_auth(request)
    if not _TIMER_AVAILABLE:
        raise HTTPException(500, "timer module not available")
    body = await request.json()
    try:
        timer = _timer_manager().add_timer(body)
    except ValueError as ve:
        return JSONResponse({"ok": False, "error": str(ve)}, status_code=400)
    return {"ok": True, "timer": timer}


@router.patch("/api/timers/{tid}")
async def api_timer_update(tid: str, request: Request):
    require_auth(request)
    if not _TIMER_AVAILABLE:
        raise HTTPException(500, "timer module not available")
    body = await request.json()
    t = _timer_manager().update_timer(tid, body)
    return {"ok": bool(t), "timer": t}


@router.delete("/api/timers/{tid}")
async def api_timer_delete(tid: str, request: Request):
    require_auth(request)
    if not _TIMER_AVAILABLE:
        raise HTTPException(500, "timer module not available")
    return {"ok": _timer_manager().delete_timer(tid)}


@router.post("/api/timers/{tid}/run")
async def api_timer_run(tid: str, request: Request):
    require_auth(request)
    if not _TIMER_AVAILABLE:
        raise HTTPException(500, "timer module not available")
    return await _timer_manager().run_now(tid)


@router.get("/api/timers/notifications")
async def api_timer_notifications(request: Request):
    require_auth(request)
    if not _TIMER_AVAILABLE:
        return {"notifications": []}
    return {"notifications": _timer_manager().get_notifications()}


@router.get("/api/timers/{tid}/history")
async def api_timer_history(tid: str, request: Request):
    require_auth(request)
    if not _TIMER_AVAILABLE:
        raise HTTPException(500, "timer module not available")
    for t in _timer_manager().list_timers():
        if t["id"] == tid:
            return {"id": tid, "history": t.get("history", [])}
    return JSONResponse({"error": "not found"}, status_code=404)
