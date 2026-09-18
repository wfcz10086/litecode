"""projects_router.py — 项目 CRUD + 共享 context + decisions + session 绑定."""
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


def _project_store():
    """Lazy import + workspace 路径解析."""
    from core.projects import ProjectStore
    _paths = S.cfg.get("paths", {})
    _agt = S.cfg.get("agent", {})
    ws = Path(_paths.get("workspace_base", _agt.get("workspace", "/tmp/litecode_workspace")))
    return ProjectStore(ws)


@router.get("/api/projects")
async def api_projects_list(request: Request, status: str = ""):
    require_auth(request)
    try:
        items = [m.to_dict() for m in _project_store().list_all(status=status or "")]
        return {"projects": items, "count": len(items)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/projects")
async def api_projects_create(request: Request):
    require_auth(request)
    try:
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        ptype = body.get("type") or body.get("project_type") or "general"
        m = _project_store().create(
            name=name,
            project_type=ptype,
            description=body.get("description", ""),
            tags=body.get("tags") or [],
        )
        return m.to_dict()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/projects/{pid}")
async def api_projects_get(pid: str, request: Request):
    require_auth(request)
    m = _project_store().get(pid)
    if not m:
        return JSONResponse({"error": "not found"}, status_code=404)
    return m.to_dict()


@router.patch("/api/projects/{pid}")
async def api_projects_update(pid: str, request: Request):
    require_auth(request)
    body = await request.json()
    allowed = {"name", "description", "status", "tags", "project_type"}
    fields = {k: v for k, v in body.items() if k in allowed}
    m = _project_store().update(pid, **fields)
    if not m:
        return JSONResponse({"error": "not found"}, status_code=404)
    return m.to_dict()


@router.delete("/api/projects/{pid}")
async def api_projects_delete(pid: str, request: Request, hard: bool = False):
    require_auth(request)
    ok = _project_store().delete(pid, soft=not hard)
    return {"ok": ok, "project_id": pid, "soft": not hard}


@router.get("/api/projects/{pid}/context")
async def api_projects_context_list(pid: str, request: Request):
    require_auth(request)
    ps = _project_store()
    m = ps.get(pid)
    if not m:
        return JSONResponse({"error": "project not found"}, status_code=404)
    pdir = ps.project_dir(pid)
    items = []
    for p in sorted(pdir.glob("*.md")):
        try:
            txt = p.read_text(errors="replace")
            items.append({"name": p.name, "size": len(txt), "preview": txt[:200]})
        except Exception:
            continue
    return {"project_id": pid, "files": items, "count": len(items)}


@router.get("/api/projects/{pid}/context/{fname}")
async def api_projects_context_get(pid: str, fname: str, request: Request):
    require_auth(request)
    if "/" in fname or ".." in fname:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    ps = _project_store()
    if not ps.get(pid):
        return JSONResponse({"error": "project not found"}, status_code=404)
    fp = ps.project_dir(pid) / fname
    if not fp.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return {"name": fname, "content": fp.read_text(errors="replace")}


@router.put("/api/projects/{pid}/context/{fname}")
async def api_projects_context_put(pid: str, fname: str, request: Request):
    require_auth(request)
    if "/" in fname or ".." in fname or not fname.endswith(".md"):
        return JSONResponse({"error": "filename must end with .md and have no path"}, status_code=400)
    ps = _project_store()
    if not ps.get(pid):
        return JSONResponse({"error": "project not found"}, status_code=404)
    body = await request.json()
    content = body.get("content", "")
    fp = ps.project_dir(pid) / fname
    fp.write_text(content)
    ps.update(pid, last_active=time.time())
    return {"ok": True, "name": fname, "size": len(content)}


@router.delete("/api/projects/{pid}/context/{fname}")
async def api_projects_context_delete(pid: str, fname: str, request: Request):
    require_auth(request)
    if "/" in fname or ".." in fname or not fname.endswith(".md"):
        return JSONResponse({"error": "filename must end with .md and have no path"}, status_code=400)
    ps = _project_store()
    if not ps.get(pid):
        return JSONResponse({"error": "project not found"}, status_code=404)
    fp = ps.project_dir(pid) / fname
    if not fp.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    fp.unlink()
    ps.update(pid, last_active=time.time())
    return {"ok": True, "name": fname}


@router.get("/api/projects/{pid}/decisions")
async def api_projects_decisions_get(pid: str, request: Request):
    require_auth(request)
    ps = _project_store()
    if not ps.get(pid):
        return JSONResponse({"error": "project not found"}, status_code=404)
    fp = ps.project_dir(pid) / "PROJECT_DECISIONS.md"
    content = fp.read_text(errors="replace") if fp.exists() else ""
    return {"project_id": pid, "content": content, "size": len(content)}


@router.post("/api/projects/{pid}/decisions")
async def api_projects_decisions_append(pid: str, request: Request):
    require_auth(request)
    ps = _project_store()
    if not ps.get(pid):
        return JSONResponse({"error": "project not found"}, status_code=404)
    body = await request.json()
    decision = (body.get("decision") or body.get("content") or "").strip()
    if not decision:
        return JSONResponse({"error": "decision/content required"}, status_code=400)
    author = body.get("author") or body.get("session_id") or "anonymous"
    fp = ps.project_dir(pid) / "PROJECT_DECISIONS.md"
    fp.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"\n## {ts} — {author}\n\n{decision}\n"
    if fp.exists():
        fp.write_text(fp.read_text(errors="replace") + line)
    else:
        fp.write_text("# PROJECT_DECISIONS\n\n本文件汇总跨 session 的决策, 自动加载到 agent 提示中。\n" + line)
    ps.update(pid, last_active=time.time())
    return {"ok": True, "size": fp.stat().st_size}


@router.delete("/api/projects/{pid}/decisions")
async def api_projects_decisions_clear(pid: str, request: Request):
    require_auth(request)
    ps = _project_store()
    if not ps.get(pid):
        return JSONResponse({"error": "project not found"}, status_code=404)
    fp = ps.project_dir(pid) / "PROJECT_DECISIONS.md"
    if fp.exists():
        fp.unlink()
    ps.update(pid, last_active=time.time())
    return {"ok": True}


@router.post("/api/projects/{pid}/sessions")
async def api_projects_bind_session(pid: str, request: Request):
    require_auth(request)
    body = await request.json()
    sid = (body.get("session_id") or "").strip()
    if not sid:
        return JSONResponse({"error": "session_id required"}, status_code=400)
    ps = _project_store()
    m = ps.get(pid)
    if not m:
        return JSONResponse({"error": "project not found"}, status_code=404)
    sf = ps.project_dir(pid) / "sessions.txt"
    existing = sf.read_text(errors="replace").splitlines() if sf.exists() else []
    if sid not in existing:
        existing.append(sid)
        sf.write_text("\n".join(existing) + "\n")
    ps.update(pid, last_active=time.time())
    return {"ok": True, "project_id": pid, "session_id": sid, "session_count": len(existing)}
