"""gateway/observability.py — clawinfo / stats / questions / map_status / health."""
from __future__ import annotations

from fastapi import APIRouter, Request

from lib.stats import questions_load as _questions_load
from lib.stats import stats_load as _stats_load

from ._deps import _srv, require_auth

router = APIRouter()


@router.get("/v1/clawinfo")
@router.get("/clawinfo")
async def clawinfo():
    srv = _srv()
    return {
        "total": 1,
        "instances": [{
            "clawid":   "1",
            "clawname": "openclaw",
            "port":     srv.PORT,
            "status":   "online",
            "model":    srv.MODEL_ID,
            "backend":  srv.BACKEND_URL
        }]
    }


@router.get("/v1/stats")
@router.get("/stats")
async def get_stats():
    """Token 使用统计"""
    s = _stats_load()
    total = s.get("total_prompt", 0) + s.get("total_completion", 0)
    days = sorted(s.get("by_day", {}).items())[-7:]
    return {
        "total_prompt_tokens":     s.get("total_prompt", 0),
        "total_completion_tokens": s.get("total_completion", 0),
        "total_tokens":            total,
        "total_calls":             s.get("total_calls", 0),
        "avg_tokens_per_call":     round(total / max(s.get("total_calls", 1), 1)),
        "last_updated":            s.get("last_updated"),
        "by_day":                  dict(days),
        "top_sessions":            sorted(
            [{"sid": k, **v} for k, v in s.get("sessions", {}).items()],
            key=lambda x: x.get("prompt", 0) + x.get("completion", 0),
            reverse=True
        )[:10],
    }


@router.get("/v1/questions")
@router.get("/questions")
async def get_questions(limit: int = 100, session: str = ""):
    """获取最近 N 条用户提问历史（不含 tool_call）"""
    qs = _questions_load()
    if session:
        qs = [q for q in qs if q.get("session") == session]
    return {
        "total":     len(qs),
        "questions": qs[-limit:][::-1],  # 最新的在前
    }


# ── Project Map 状态（从 server.py 移植）───────────────────────
@router.get("/v1/map_status")
async def map_status():
    try:
        from project_map_watcher import get_global_watcher  # type: ignore
        w = get_global_watcher()
        return {"enabled": True,
                "watched": len(w._watched),
                "projects": [await w.get_summary(r) for r in list(w._watched)[:5]]}
    except (ImportError, Exception):
        return {"enabled": False}


@router.post("/v1/map_force_update")
async def map_force_update(request: Request):
    require_auth(request)
    srv = _srv()
    body = await request.json()
    project_root = body.get("project_root", str(srv.WORKSPACE))
    try:
        from project_map_watcher import get_global_watcher  # type: ignore
        get_global_watcher().force_update(project_root)
        return {"ok": True, "project_root": project_root}
    except (ImportError, Exception) as e:
        return {"ok": False, "error": str(e)}


@router.get("/health")
async def health():
    srv = _srv()
    return {
        "status": "ok",
        "model": srv.MODEL_ID,
        "backend_url": srv.BACKEND_URL,
        "backend_type": srv.BACKEND_TYPE,
        "port": srv.PORT,
    }
