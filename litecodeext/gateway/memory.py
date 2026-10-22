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


# ── 记忆可见可删: 全局用户档案 / 会话条目 / 跨会话索引 ─────────────────────
# 用户在「记忆」面板里看到模型记住了什么, 并能逐条删除、纠正 (ChatGPT "管理记忆" 的对应物)。
# 默认是主人档案; 传 ?sid=wx-… 看某个微信联系人的独立档案。
def _profile_mod():
    try:
        from memory import profile as p
    except ImportError:
        from core.memory import profile as p
    return p


def _store(sid: str = ""):
    return _profile_mod().ProfileStore.for_session(_srv().WORKSPACE, sid or None)


@router.get("/v1/profile")
async def get_profile_api(request: Request, sid: str = ""):
    require_auth(request)
    p = _profile_mod()
    store = _store(sid)
    md = store.user_md.read_text(errors="replace") if store.user_md.exists() else ""
    return {"profile_id": store.profile_id, "facts": [p.as_dict(f) for f in store.list()],
            "user_md": md, "user_md_path": str(store.user_md)}


@router.post("/v1/profile/facts")
async def add_profile_fact_api(request: Request, sid: str = ""):
    require_auth(request)
    body = await request.json()
    action, fact, old = _store(sid).upsert(str(body.get("text") or ""), key=body.get("key") or None,
                                           source="user_ui", importance=3)
    if action == "rejected":
        raise HTTPException(400, "empty or untrusted text")
    _profile_mod().sync_index(_srv().WORKSPACE, action, fact, old)
    return {"ok": True, "action": action, "fact": fact, "replaced": old}


@router.patch("/v1/profile/facts/{fid}")
async def edit_profile_fact_api(fid: str, request: Request, sid: str = ""):
    require_auth(request)
    body = await request.json()
    store = _store(sid)
    before = store.get(fid)
    fact = store.correct(fid, str(body.get("text") or ""))
    if fact is None:
        raise HTTPException(404, "fact not found or text invalid")
    _profile_mod().sync_index(_srv().WORKSPACE, "updated", fact, before and before.get("text"))
    return {"ok": True, "fact": fact}


@router.delete("/v1/profile/facts/{fid}")
async def delete_profile_fact_api(fid: str, request: Request, sid: str = ""):
    require_auth(request)
    gone = _store(sid).forget(fid=fid)
    for f in gone:
        _profile_mod().sync_index(_srv().WORKSPACE, "forgotten", f)
    if not gone:
        raise HTTPException(404, "fact not found")
    return {"ok": True, "deleted": gone}


@router.put("/v1/profile/user_md")
async def put_user_md_api(request: Request, sid: str = ""):
    require_auth(request)
    body = await request.json()
    store = _store(sid)
    store.root.mkdir(parents=True, exist_ok=True)
    store.user_md.write_text(str(body.get("content") or ""))
    return {"ok": True, "path": str(store.user_md)}


@router.delete("/v1/memory/{sid}/entry")
async def delete_memory_entry_api(sid: str, request: Request):
    """删本会话 MEMORY.md 里含 text 的条目行 (section 可选)。"""
    require_auth(request)
    body = await request.json()
    mgr = _mem_get(**_mem_kwargs(sid))
    n = mgr.forget(str(body.get("text") or ""), section=body.get("section") or None)
    return {"ok": n > 0, "deleted": n}


@router.get("/v1/memory_index")
async def list_memory_index_api(request: Request, q: str = "", category: str = "", limit: int = 50):
    require_auth(request)
    from lib.config import HAS_MEMORY_INDEX, memory_index_get
    if not HAS_MEMORY_INDEX:
        return {"items": []}
    idx = memory_index_get(_srv().WORKSPACE)
    return {"items": idx.list_recent(limit=min(max(limit, 1), 200), category=category or None, query=q)}


@router.delete("/v1/memory_index/{rid}")
async def delete_memory_index_api(rid: int, request: Request):
    require_auth(request)
    from lib.config import HAS_MEMORY_INDEX, memory_index_get
    if not HAS_MEMORY_INDEX:
        raise HTTPException(503, "memory index not available")
    if not memory_index_get(_srv().WORKSPACE).delete(rid):
        raise HTTPException(404, "not found")
    return {"ok": True}
