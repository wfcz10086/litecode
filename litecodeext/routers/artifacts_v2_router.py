"""artifacts_v2_router.py — owner-scoped artifact API (S1-N).

区别于 memory_router.py 里的 /api/artifacts/{sid} (session-scope 老概念),
本 router 走 /api/artifacts_v2/* 命名空间, 服务 timer/bg/plugin 通用产出物.

命名 v2 是为避免和老 URL 撞车; 后续 v1 老概念可能整合过来.
"""
from __future__ import annotations
import mimetypes
from fastapi import APIRouter, Request, Response, HTTPException, Query

from routers.auth_router import require_auth
from plugins.artifacts.store import get_store


router = APIRouter(prefix="/api/artifacts_v2", tags=["artifacts"])


_KIND_MIME = {
    "dxf": "application/dxf",
    "stl": "application/vnd.ms-pki.stl",
    "step": "application/step",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
    "html": "text/html; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "log": "text/plain; charset=utf-8",
    "json": "application/json",
    "bytes": "application/octet-stream",
}


def _mime_for(kind: str, name: str) -> str:
    if kind in _KIND_MIME:
        return _KIND_MIME[kind]
    if name:
        guess, _ = mimetypes.guess_type(name)
        if guess:
            return guess
    return "application/octet-stream"


@router.get("")
async def list_artifacts(request: Request,
                          owner: str | None = Query(None),
                          prefix: str | None = Query(None),
                          limit: int = Query(200, ge=1, le=2000)):
    """列出 artifact.

    ?owner=timer:daily-sync           只看某 owner
    ?prefix=plugin:                   前缀过滤
    (无参数 → 全量, 按 ts 降序, 截断到 limit)
    """
    require_auth(request)
    store = get_store()
    if owner:
        recs = store.list_by_owner(owner)[:limit]
    else:
        recs = store.list_all(prefix=prefix, limit=limit)
    return {
        "count": len(recs),
        "items": [r.to_public_dict() for r in recs],
    }


@router.get("/owners")
async def list_owners(request: Request):
    """所有已知 owner 名 (给 Timer/BG/Plugin 三个 tab 拉侧栏用)."""
    require_auth(request)
    return {"owners": get_store().owners()}


@router.get("/meta/{aid}")
async def get_meta(aid: str, request: Request):
    require_auth(request)
    m = get_store().meta(aid)
    if not m:
        raise HTTPException(status_code=404, detail="artifact not found")
    return m.to_public_dict()


@router.get("/blob/{aid}")
async def get_blob(aid: str, request: Request,
                    download: int = Query(0, description="1 → attachment; 0 → inline")):
    require_auth(request)
    store = get_store()
    m = store.meta(aid)
    if not m:
        raise HTTPException(status_code=404, detail="artifact not found")
    data = store.blob(aid)
    if data is None:
        raise HTTPException(status_code=404, detail="blob missing")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{m.name}"'
    return Response(content=data,
                    media_type=_mime_for(m.kind, m.name),
                    headers=headers)


@router.delete("/{aid}")
async def delete_artifact(aid: str, request: Request):
    require_auth(request)
    ok = get_store().delete(aid)
    if not ok:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"ok": True, "aid": aid}


@router.delete("/owner/{owner}")
async def purge_owner(owner: str, request: Request):
    require_auth(request)
    n = get_store().purge_owner(owner)
    return {"ok": True, "owner": owner, "purged_files": n}
