"""docker_router.py — Docker 管理 REST API (#44).

薄壳: 全部转调 plugins/tools/docker_ops.py 的 async helpers.
鉴权: 复用 require_auth (跟 workspace / dag / timer 一样).

GET  /api/docker/ps?all=0|1
GET  /api/docker/logs?name=<name>&tail=100
GET  /api/docker/stats
GET  /api/docker/inspect?name=<name>
POST /api/docker/restart   body {"name": "..."}
POST /api/docker/stop      body {"name": "..."}
POST /api/docker/start     body {"name": "..."}
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth

router = APIRouter()


def _ops():
    """延迟 import, 避免启动时 httpx socket path missing 就崩."""
    import importlib
    return importlib.import_module("plugins.tools.docker_ops")


@router.get("/api/docker/ps")
async def api_docker_ps(request: Request, all: int = 0):
    require_auth(request)
    return await _ops().docker_ps(all=bool(all))


@router.get("/api/docker/logs")
async def api_docker_logs(request: Request, name: str = "", tail: int = 100):
    require_auth(request)
    if not name:
        raise HTTPException(400, "name required")
    return await _ops().docker_logs(name=name, tail=tail)


@router.get("/api/docker/stats")
async def api_docker_stats(request: Request):
    require_auth(request)
    return await _ops().docker_stats()


@router.get("/api/docker/inspect")
async def api_docker_inspect(request: Request, name: str = ""):
    require_auth(request)
    if not name:
        raise HTTPException(400, "name required")
    return await _ops().docker_inspect(name=name)


@router.post("/api/docker/restart")
async def api_docker_restart(request: Request, body: dict):
    require_auth(request)
    name = str((body or {}).get("name", "")).strip()
    if not name:
        raise HTTPException(400, "name required")
    return await _ops().docker_restart(name=name)


@router.post("/api/docker/stop")
async def api_docker_stop(request: Request, body: dict):
    require_auth(request)
    name = str((body or {}).get("name", "")).strip()
    if not name:
        raise HTTPException(400, "name required")
    return await _ops().docker_stop(name=name)


@router.post("/api/docker/start")
async def api_docker_start(request: Request, body: dict):
    require_auth(request)
    name = str((body or {}).get("name", "")).strip()
    if not name:
        raise HTTPException(400, "name required")
    return await _ops().docker_start(name=name)
