"""health_router.py — 健康 / 统计 / map / bgprocs."""
import asyncio
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth
from routers._body import json_object
from routers.state import S

router = APIRouter()
ORDER = 10  # 注册顺序 (routers.discover 按此升序 include, 路径重叠时先注册者生效)


from lib import version as _version  # noqa: E402
from routers.upload_router import max_upload_mb  # noqa: E402


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
                "max_upload_mb": max_upload_mb(),
                "multimodal_max_images": int(_ui_cfg.get("multimodal_max_images", 6)),
                "supports_vision": bool(S.cfg.get("model", {}).get("supports_vision", False)),
                **_version.info(),
            }
    except Exception:
        # 上游挂了也给上传上限, 否则前端回落到 20MB 默认把大文件挡在浏览器端
        return {"ok": False, "model": S.model, "ctx": S.ctx_window,
                "max_upload_mb": max_upload_mb(), **_version.info()}


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
    body = await json_object(request)
    d = await S.aproxy("/v1/map_force_update", "POST", json=body)
    return d or {"error": "server unavailable"}


@router.get("/api/bgprocs")
async def api_bgprocs(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"procs": []}
    d = await S.aproxy("/v1/bgprocs")
    return d or {"procs": []}


@router.get("/api/bgprocs/{pid}/tail")
async def api_bgprocs_tail(pid: str, request: Request, lines: int = 100):
    """[ui-audit 2026-10-21] 后台面板「日志」按钮一直 404 (前端显示"tail 端点尚未实现")。
    只读 server 登记过的该 pid 的 log 文件 (路径来自 /v1/bgprocs, 不接受前端传路径), 取末尾 N 行。"""
    require_auth(request)
    if not pid.isdigit():
        raise HTTPException(400, "invalid pid")
    lines = max(1, min(int(lines or 100), 2000))
    if S.aproxy is None:
        raise HTTPException(503, "server unavailable")
    d = await S.aproxy("/v1/bgprocs")
    procs = (d or {}).get("procs") or []
    info = next((p for p in procs if str(p.get("pid")) == pid), None)
    if not info:
        raise HTTPException(404, "后台任务不存在或已结束")
    log = str(info.get("log") or "")
    if not log:
        return {"pid": pid, "log": "", "note": "该任务没有登记日志文件"}
    fp = Path(log)
    if not fp.is_file():
        return {"pid": pid, "log": "", "note": f"日志文件不存在: {log}"}

    def _tail() -> str:
        with fp.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])

    return {"pid": pid, "log": await asyncio.get_event_loop().run_in_executor(None, _tail), "path": log}


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
