"""record_router.py — 录屏模式接口 (lib/screen_record.py)。

GET  /api/chat/{sid}/record          该会话是否开了自动录屏
POST /api/chat/{sid}/record          {"on": true/false} 开关 (只影响这个对话; 下一轮开始生效)
POST /api/screenrec/{sid}            用户手动录的当前标签页视频 (webm) → 转成 mp4, 返回路径 (上限 300MB)
GET  /api/chat/{sid}/recordings      这个对话的录屏列表 (导出用)
"""
import asyncio
import time

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from routers.auth_router import require_auth
from routers._body import json_object

router = APIRouter()
ORDER = 60
_MAX_REC = 300 * 1024 * 1024


def _wui():
    import web_ui
    return web_ui


def _owned_session(sid: str, request: Request) -> dict:
    from lib.owner import require_owner
    s = _wui().load_session(sid)
    require_owner(s, request)
    return s


@router.get("/api/chat/{sid}/record")
async def record_state(sid: str, request: Request):
    require_auth(request)
    s = _owned_session(sid, request)
    return {"on": bool(s.get("record"))}


@router.post("/api/chat/{sid}/record")
async def record_toggle(sid: str, request: Request):
    require_auth(request)
    body = await json_object(request)
    w = _wui()
    s = _owned_session(sid, request)
    s["record"] = bool(body.get("on"))
    w.save_session(s)
    return {"ok": True, "on": s["record"]}


@router.get("/api/chat/{sid}/recordings")
async def record_list(sid: str, request: Request):
    require_auth(request)
    _owned_session(sid, request)
    from lib.screen_record import rec_dir
    items = []
    for p in sorted(rec_dir(sid).glob("*.mp4"), reverse=True):
        st = p.stat()
        items.append({"path": str(p), "name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return {"items": items}


@router.post("/api/screenrec/{sid}")
async def screenrec_upload(sid: str, request: Request, file: UploadFile = File(...)):
    require_auth(request)
    _owned_session(sid, request)
    from lib.screen_record import rec_dir, transcode_to_mp4
    src = rec_dir(sid) / f"user-{time.strftime('%Y%m%d-%H%M%S')}.webm"
    size = 0
    with open(src, "wb") as out:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_REC:
                out.close()
                src.unlink(missing_ok=True)
                raise HTTPException(413, "录屏超过 300MB, 请录短一点")
            out.write(chunk)
    try:
        mp4 = await asyncio.to_thread(transcode_to_mp4, src)
    except Exception as e:
        raise HTTPException(500, f"转 mp4 失败: {str(e)[:200]}")
    src.unlink(missing_ok=True)
    return {"ok": True, "path": str(mp4), "name": mp4.name, "size": mp4.stat().st_size}
