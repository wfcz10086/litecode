"""health_router.py — 健康 / 统计 / map / bgprocs."""
import asyncio
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


@router.get("/api/health")
async def api_health(request: Request):
    require_auth(request)
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{S.server_url}/health", headers=S.headers)
            d = r.json() if r.status_code == 200 else {}
            _ui_cfg = S.cfg.get("web_ui", {}) or {}
            return {
                "ok": r.status_code == 200,
                "model": d.get("model", S.model),
                "ctx": S.ctx_window,
                "backend_url": d.get("backend_url", ""),
                "backend_type": d.get("backend_type", ""),
                "max_upload_mb": int(_ui_cfg.get("max_upload_mb", 20)),
                "multimodal_max_images": int(_ui_cfg.get("multimodal_max_images", 6)),
                "supports_vision": bool(S.cfg.get("model", {}).get("supports_vision", False)),
            }
    except Exception:
        return {"ok": False, "model": S.model, "ctx": S.ctx_window}


@router.get("/api/questions")
async def api_questions(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"questions": []}
    d = await S.aproxy("/v1/questions")
    return d or {"questions": []}


# /api/stats 留在 web_ui.py — 它依赖 _calc_cost + _PRICE_TABLE, 不搬


@router.get("/api/map_status")
async def api_map_status(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"error": "server unavailable"}
    d = await S.aproxy("/v1/map_status")
    return d or {"error": "server unavailable"}


@router.post("/api/map_force_update")
async def api_map_force_update(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"error": "server unavailable"}
    body = await request.json()
    d = await S.aproxy("/v1/map_force_update", "POST", json=body)
    return d or {"error": "server unavailable"}


@router.get("/api/bgprocs")
async def api_bgprocs(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"procs": []}
    d = await S.aproxy("/v1/bgprocs")
    return d or {"procs": []}


@router.delete("/api/bgprocs/{pid}")
async def api_bgprocs_kill(pid: str, request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    d = await S.aproxy(f"/v1/bgprocs/{pid}", "DELETE")
    return d or {"ok": False, "error": "server unavailable"}


# ── [2026-07-25] plugin registry 内省 + 热重扫 ────────────────
# 场景: mount 进容器的 plugins/tools/*.py 改动生效需重启容器 (registry 是
# 启动时 scan_once_if_empty). 这两个端点让运维/CI/自迭代脚本免重启即可重新
# 扫描, 并观察 ok/err 分布. 只读列表 + 破坏性重扫都要 require_auth.
@router.get("/api/plugins")
async def api_plugins_list(request: Request):
    require_auth(request)
    try:
        from plugins.registry import registry, scan_once_if_empty
        scan_once_if_empty()
        specs = registry.list()
        errs = registry.load_errors()
        return {
            "ok": True,
            "count": len(specs),
            "errors": [{"path": p, "err": e} for p, e in errs],
            "tools": [
                {"name": s.name, "origin": s.origin, "version": s.version,
                 "source": s.source_path, "capabilities": s.capabilities}
                for s in sorted(specs, key=lambda ss: ss.name)
            ],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/api/plugins/reload")
async def api_plugins_reload(request: Request):
    require_auth(request)
    try:
        from plugins.registry import registry
        before = len(registry.names())
        n = registry.scan()
        errs = registry.load_errors()
        return {
            "ok": True,
            "count_before": before,
            "count_after": n,
            "error_count": len(errs),
            "errors": [{"path": p, "err": e[:200]} for p, e in errs],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
