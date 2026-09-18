"""wecom_router.py — 企业微信智能机器人 REST API.

对齐 wechat_router 8 端点 + 加 contacts / messages / upload.
"""
import os
import tempfile

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse

from routers.auth_router import require_auth

router = APIRouter()

try:
    from wecom_bridge import get_manager as _wc_manager  # type: ignore
    _WC_AVAILABLE = True
except ImportError:
    _WC_AVAILABLE = False
    _wc_manager = None  # type: ignore


def _need_wc():
    if not _WC_AVAILABLE or _wc_manager is None:
        raise HTTPException(500, "wecom bridge not available")
    return _wc_manager()


# ── 状态 / 配置 ──────────────────────────────────────────────
@router.get("/api/wecom/status")
async def wc_status(request: Request):
    require_auth(request)
    if not _WC_AVAILABLE or _wc_manager is None:
        return {"available": False}
    mgr = _wc_manager()
    return {"available": True, "config": mgr.get_config(),
            "bots": mgr.list_bots()}


@router.post("/api/wecom/config")
async def wc_update_config(request: Request):
    require_auth(request)
    mgr = _need_wc()
    body = await request.json()
    return mgr.update_config(body or {})


# ── bots CRUD ────────────────────────────────────────────────
@router.post("/api/wecom/bots")
async def wc_add_bot(request: Request):
    require_auth(request)
    mgr = _need_wc()
    body = await request.json() or {}
    bot_id = (body.get("bot_id") or "").strip()
    secret = (body.get("secret") or "").strip()
    if not bot_id or not secret:
        raise HTTPException(400, "bot_id / secret 必填")
    return await mgr.add_bot(bot_id, secret)


@router.delete("/api/wecom/bots/{bot_id}")
async def wc_remove_bot(bot_id: str, request: Request):
    require_auth(request)
    mgr = _need_wc()
    return await mgr.remove_bot(bot_id)


@router.post("/api/wecom/bots/{bot_id}/relogin")
async def wc_relogin(bot_id: str, request: Request):
    require_auth(request)
    mgr = _need_wc()
    return await mgr.relogin(bot_id)


@router.get("/api/wecom/bots/{bot_id}")
async def wc_bot_info(bot_id: str, request: Request):
    require_auth(request)
    mgr = _need_wc()
    bot = mgr.bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    return bot.info()


# ── 发送 (text/markdown/file/image) ──────────────────────────
@router.post("/api/wecom/bots/{bot_id}/send")
async def wc_send(bot_id: str, request: Request):
    """body: {"to": userid, "text"|"markdown"|"file_path"|"image_path": ...,
              "chat_type": 1}
    """
    require_auth(request)
    mgr = _need_wc()
    body = await request.json() or {}
    to = (body.get("to") or "").strip()
    if not to:
        return JSONResponse({"error": "missing to (userid)"}, status_code=400)
    chat_type = int(body.get("chat_type", 1))
    r = await mgr.send(bot_id, to,
                       text=body.get("text", ""),
                       markdown=body.get("markdown", ""),
                       file_path=body.get("file_path", ""),
                       image_path=body.get("image_path", ""),
                       chat_type=chat_type)
    if not r.get("ok"):
        return JSONResponse(r, status_code=422 if "rate_limit" in
                            str(r.get("errmsg", "")) else 500)
    return r


# ── 上传文件 (前端选文件 → 上传到 CDN 得 media_id) ─────────
@router.post("/api/wecom/bots/{bot_id}/upload")
async def wc_upload(bot_id: str, request: Request,
                    file: UploadFile = File(...),
                    mtype: str = Form("file")):
    require_auth(request)
    mgr = _need_wc()
    bot = mgr.bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    if bot.status != "online":
        raise HTTPException(503, f"bot status={bot.status}")
    if mtype not in ("file", "image", "voice", "video"):
        mtype = "file"
    # 落盘 tmp
    suffix = os.path.splitext(file.filename or "upload.bin")[1] or ".bin"
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        data = await file.read()
        tf.write(data)
        tf.close()
        media_id = await bot.upload_media(tf.name, mtype)
        if not media_id:
            raise HTTPException(500, "upload failed")
        return {"ok": True, "media_id": media_id,
                "filename": file.filename, "size": len(data), "mtype": mtype}
    finally:
        try:
            os.unlink(tf.name)
        except Exception:
            pass


# ── 联系人 / 消息历史 ────────────────────────────────────────
@router.get("/api/wecom/bots/{bot_id}/contacts")
async def wc_contacts(bot_id: str, request: Request, limit: int = 200):
    require_auth(request)
    mgr = _need_wc()
    bot = mgr.bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    return {"bot_id": bot_id, "contacts": bot.store.list_contacts(limit)}


@router.get("/api/wecom/bots/{bot_id}/messages")
async def wc_messages(bot_id: str, request: Request,
                      userid: str = "", limit: int = 50):
    require_auth(request)
    mgr = _need_wc()
    bot = mgr.bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    return {"bot_id": bot_id, "userid": userid,
            "messages": bot.store.list_messages(userid, limit)}


@router.delete("/api/wecom/bots/{bot_id}/messages")
async def wc_clear_messages(bot_id: str, request: Request, userid: str = ""):
    """清空消息历史 (SQLite messages 表). contacts / media / rate_limit 不动.
    userid 参数可选: 传了只清该联系人, 不传清全部.
    """
    require_auth(request)
    mgr = _need_wc()
    bot = mgr.bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    deleted = bot.store.clear_messages(userid)
    return {"ok": True, "bot_id": bot_id, "userid": userid, "deleted": deleted}
