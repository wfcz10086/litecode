"""gateway/observability.py — clawinfo / stats / questions / map_status / health."""
from __future__ import annotations

from fastapi import APIRouter, Request

from lib.stats import questions_load as _questions_load
from lib.stats import stats_load as _stats_load

from ._deps import _auth, _srv, require_auth

router = APIRouter()

# [sec 2026-10-19] /v1/questions 单次最多返回条数 (limit<=0 / 超大值都夹到这个范围)
_QUESTIONS_MAX = 500


@router.get("/v1/clawinfo")
@router.get("/clawinfo")
async def clawinfo(request: Request):
    # [sec 2026-10-19] 暴露 model / backend_url, 需网关 Bearer token
    require_auth(request)
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
async def get_stats(request: Request):
    """Token 使用统计 (需网关 Bearer token)"""
    require_auth(request)
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
async def get_questions(request: Request, limit: int = 100, session: str = ""):
    """获取最近 N 条用户提问历史（不含 tool_call）; 需网关 Bearer token."""
    require_auth(request)
    # [sec 2026-10-19] limit=0 时 qs[-0:] 会返回全部 → 夹到 [1, _QUESTIONS_MAX]
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, _QUESTIONS_MAX))
    qs = _questions_load()
    if session:
        qs = [q for q in qs if q.get("session") == session]
    return {
        "total":     len(qs),
        "questions": qs[-limit:][::-1],  # 最新的在前
    }


# ── Project Map 状态（从 server.py 移植）───────────────────────
@router.get("/v1/map_status")
async def map_status(request: Request):
    require_auth(request)
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


from lib import version as _version  # noqa: E402


@router.get("/health")
async def health(request: Request):
    """公开存活探针: 未带 token 只回 status + 版本; 带合法网关 Bearer token 才回模型/后端细节
    (web_ui /api/health、swe_bench、test_full 都带 token, 字段不变)。"""
    if not _auth(request):
        return {"status": "ok", **_version.info()}
    srv = _srv()
    return {
        "status": "ok",
        "model": srv.MODEL_ID,
        "backend_url": srv.BACKEND_URL,
        "backend_type": srv.BACKEND_TYPE,
        "port": srv.PORT,
        **_version.info(),
    }
