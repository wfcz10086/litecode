"""plugins/kb/router.py — /api/kb 接口 (经 routers/kb_router.py 自动挂载)。

全部要求 LiteCode 登录 (cookie); /api/kb/search 另外接受本机 litecode_server 用 config server.token
调用 (头 X-LiteCode-Token), 供 kb_search 工具复用 web_ui 进程里那一份向量模型。
知识库未启用时各接口回 {"enabled": false} 或 503, 不报错刷屏。
"""
from __future__ import annotations

import hmac
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import cfg, config_path, data_dir, enabled, jobs, model_dir, workspace
from .text import SUPPORTED

router = APIRouter()

_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp", ".txt": "text/plain; charset=utf-8",
         ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8",
         ".json": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8"}


def _err(code: int, msg: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _server_token() -> str:
    try:
        return str((json.loads(config_path().read_text(encoding="utf-8")).get("server") or {}).get("token") or "")
    except Exception:  # noqa: BLE001
        return ""


def _auth(request: Request, allow_token: bool = False) -> None:
    if allow_token:
        tok, want = request.headers.get("x-litecode-token", ""), _server_token()
        if tok and want and hmac.compare_digest(tok, want):
            return
    from plugins.auth.core import require_auth
    require_auth(request)


async def _call(fn, *a, **kw):
    """跑同步存储函数; KBError → 400, 其他异常 → 500 (中文原因)。"""
    from .store import KBError
    if not enabled():
        return _err(503, "知识库未启用 (config.json 缺 kb.dsn), 部署见 docs/KNOWLEDGE_BASE.md")
    try:
        return await run_in_threadpool(fn, *a, **kw)
    except KBError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        return _err(500, f"{type(e).__name__}: {e}")


def _is_resp(x) -> bool:
    return isinstance(x, JSONResponse)


# ── 状态 / 库 ─────────────────────────────────────────────────────

@router.get("/api/kb/status")
async def kb_status(request: Request):
    _auth(request)
    c = cfg()
    md = model_dir()
    out = {"enabled": enabled(), "model_ok": md is not None, "model_dir": str(md or ""),
           "supported": sorted(SUPPORTED),
           "config": {k: c[k] for k in ("chunk_size", "chunk_overlap", "top_k", "min_score", "context_chars", "total_chars", "ocr")}}
    if not out["enabled"]:
        out["hint"] = "config.json 加 kb 段 (dsn 指向带 pgvector 的 PostgreSQL), 部署见 docs/KNOWLEDGE_BASE.md"
        return out
    from . import store
    out.update(await run_in_threadpool(store.status))
    if out.get("db_ok"):
        jobs.start()
    return out


@router.get("/api/kb/bases")
async def kb_bases(request: Request):
    _auth(request)
    if not enabled():
        return {"enabled": False, "bases": []}
    from . import store
    r = await _call(store.list_bases)
    if not _is_resp(r):
        jobs.start()
    return r if _is_resp(r) else {"enabled": True, "bases": r}


@router.post("/api/kb/bases")
async def kb_create(request: Request):
    _auth(request)
    from . import store
    b = await request.json()
    r = await _call(store.create_base, str(b.get("name") or ""), str(b.get("description") or ""))
    return r if _is_resp(r) else {"ok": True, "base": r}


@router.put("/api/kb/bases/{kb_id}")
async def kb_update(kb_id: int, request: Request):
    _auth(request)
    from . import store
    b = await request.json()
    r = await _call(store.update_base, kb_id, b.get("name"), b.get("description"))
    return r if _is_resp(r) else {"ok": True, "base": r}


@router.delete("/api/kb/bases/{kb_id}")
async def kb_delete(kb_id: int, request: Request):
    _auth(request)
    from . import store
    r = await _call(store.delete_base, kb_id)
    if _is_resp(r):
        return r
    if r:
        jobs.remove_kb_dir(kb_id)
    return {"ok": bool(r)}


# ── 文档 ──────────────────────────────────────────────────────────

@router.get("/api/kb/bases/{kb_id}/docs")
async def kb_docs(kb_id: int, request: Request):
    _auth(request)
    from . import store
    r = await _call(store.list_documents, kb_id)
    return r if _is_resp(r) else {"docs": r}


def _register(kb_id: int, src: Path, filename: str, content_hash: str, size: int, source: str, move: bool) -> dict:
    from . import store
    from .store import KBError
    if store.get_base(kb_id) is None:
        raise KBError("知识库不存在")
    ext = jobs.norm_ext(filename)
    if ext not in SUPPORTED:
        raise KBError(f"不支持的文件类型 {ext or '(无扩展名)'}")
    jobs.store_file(kb_id, src, content_hash, ext, move=move)
    doc, created = store.add_document(kb_id, filename, ext, content_hash, size, source)
    if created:
        jobs.notify()
    return {"ok": True, "doc": doc, "duplicate": not created}


@router.post("/api/kb/bases/{kb_id}/upload")
async def kb_upload(kb_id: int, request: Request):
    """multipart 单文件 (字段名 file); 前端多文件逐个上传以显示各自进度。"""
    _auth(request)
    if not enabled():
        return _err(503, "知识库未启用")
    from routers.upload_router import receive_upload
    inc = jobs.kb_dir(kb_id) / "incoming"
    inc.mkdir(parents=True, exist_ok=True)
    res = await receive_upload(request, inc, int(cfg()["max_file_mb"]) * 1048576)
    if _is_resp(res):
        return res
    fp = Path(res["path"])
    r = await _call(_register, kb_id, fp, res["name"], res["sha256"], int(res["size"]), "upload", True)
    if _is_resp(r):
        fp.unlink(missing_ok=True)
    return r


@router.post("/api/kb/bases/{kb_id}/import_path")
async def kb_import_path(kb_id: int, request: Request):
    """把工作区里已有的文件 (如对话附件 uploads/xxx.pdf) 加入知识库; 只接受 WORKSPACE 内的普通文件。"""
    _auth(request)
    b = await request.json()
    raw = str(b.get("path") or "").strip()
    ws = workspace().resolve()
    p = Path(raw)
    p = (p if p.is_absolute() else ws / p).resolve()
    kb_root = data_dir().resolve()
    if not raw or ws not in p.parents or kb_root == p or kb_root in p.parents:
        return _err(400, "只能导入工作区内的文件 (不含知识库自身目录)")
    if not p.is_file():
        return _err(404, "文件不存在")
    if p.stat().st_size > int(cfg()["max_file_mb"]) * 1048576:
        return _err(413, "文件太大")
    h = await run_in_threadpool(jobs.sha256_file, p)
    return await _call(_register, kb_id, p, p.name, h, p.stat().st_size, "path", False)


@router.post("/api/kb/docs/{doc_id}/retry")
async def kb_retry(doc_id: int, request: Request):
    _auth(request)
    from . import store
    r = await _call(store.retry_document, doc_id)
    if _is_resp(r):
        return r
    if r:
        jobs.notify()
    return {"ok": bool(r)}


@router.delete("/api/kb/docs/{doc_id}")
async def kb_doc_delete(doc_id: int, request: Request):
    _auth(request)
    from . import store
    r = await _call(store.delete_document, doc_id)
    if _is_resp(r):
        return r
    if r:
        jobs.remove_file(r["kb_id"], r["content_hash"], r["ext"])
    return {"ok": bool(r)}


@router.get("/api/kb/docs/{doc_id}/chunks")
async def kb_chunks(doc_id: int, request: Request, offset: int = 0, limit: int = 50):
    _auth(request)
    from . import store
    r = await _call(store.list_chunks, doc_id, offset, limit)
    return r if _is_resp(r) else {"chunks": r}


@router.get("/api/kb/docs/{doc_id}/text")
async def kb_doc_text(doc_id: int, request: Request, start: int = 0, length: int = 20000):
    _auth(request)
    from . import store
    doc = await _call(store.get_document, doc_id)
    if _is_resp(doc):
        return doc
    if not doc:
        return _err(404, "文档不存在")
    t = await _call(store.doc_text, doc_id, start, min(max(length, 1), 200000))
    return t if _is_resp(t) else {"doc": doc, "start": max(0, start), "text": t}


@router.get("/api/kb/docs/{doc_id}/file")
async def kb_doc_file(doc_id: int, request: Request, download: int = 0):
    _auth(request)
    from . import store
    doc = await _call(store.get_document, doc_id)
    if _is_resp(doc):
        return doc
    if not doc:
        return _err(404, "文档不存在")
    try:
        p = jobs.file_path(doc["kb_id"], doc["content_hash"], doc["ext"])
    except ValueError:
        return _err(400, "非法路径")
    if not p.is_file():
        return _err(404, "原始文件不存在")
    mime = _MIME.get(doc["ext"], "application/octet-stream")
    return FileResponse(str(p), media_type=mime, filename=doc["filename"],
                        content_disposition_type="attachment" if download or mime.endswith("octet-stream") else "inline")


# ── 检索 ──────────────────────────────────────────────────────────

@router.post("/api/kb/search")
async def kb_search(request: Request):
    _auth(request, allow_token=True)
    b = await request.json()
    q = str(b.get("query") or "").strip()
    if not q:
        return _err(400, "query 不能为空")
    if not enabled():
        return {"error": "知识库未启用"}
    from .tool import do_search
    kb = b.get("kb_id") if b.get("kb_id") not in (None, "") else b.get("kb")
    top_k = b.get("top_k")
    return await run_in_threadpool(do_search, q, kb, int(top_k) if top_k else None)
