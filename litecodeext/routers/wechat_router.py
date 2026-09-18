"""wechat_router.py — 微信桥状态 / bots / 二维码."""
import json
import pathlib
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


try:
    from wechat_bridge import get_manager as _wx_manager  # type: ignore
    _WX_AVAILABLE = True
except ImportError:
    _WX_AVAILABLE = False
    _wx_manager = None  # type: ignore


def _need_wx():
    if not _WX_AVAILABLE:
        raise HTTPException(500, "wechat bridge not available")
    return _wx_manager()


@router.get("/api/wechat/status")
async def wx_status(request: Request):
    require_auth(request)
    if not _WX_AVAILABLE:
        return {"available": False}
    mgr = _wx_manager()
    return {"available": True, "config": mgr.get_config(), "bots": mgr.list_bots()}


@router.post("/api/wechat/config")
async def wx_update_config(request: Request):
    require_auth(request)
    mgr = _need_wx()
    body = await request.json()
    return mgr.update_config(body)


@router.post("/api/wechat/bots")
async def wx_add_bot(request: Request):
    require_auth(request)
    mgr = _need_wx()
    body = await request.json()
    return await mgr.add_bot(body.get("bot_id"))


@router.delete("/api/wechat/bots/{bot_id}")
async def wx_remove_bot(bot_id: str, request: Request):
    require_auth(request)
    mgr = _need_wx()
    return await mgr.remove_bot(bot_id)


@router.post("/api/wechat/bots/{bot_id}/relogin")
async def wx_relogin(bot_id: str, request: Request):
    require_auth(request)
    mgr = _need_wx()
    return await mgr.relogin(bot_id)


@router.get("/api/wechat/bots/{bot_id}")
async def wx_bot_info(bot_id: str, request: Request):
    require_auth(request)
    mgr = _need_wx()
    bot = mgr.bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    return bot.info()


@router.post("/api/wechat/bots/{bot_id}/send")
async def wx_bot_send(bot_id: str, request: Request):
    """主动通过指定 bot 发送一条文本消息.
    body: {"to": <wx_user_id>, "text": <消息内容>}
    """
    require_auth(request)
    mgr = _need_wx()
    body = await request.json()
    to = (body or {}).get("to", "").strip()
    text = (body or {}).get("text", "")
    if not to or not text:
        return JSONResponse({"error": "missing to/text"}, status_code=400)
    bot = mgr.bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    inner = getattr(bot, "_bot", None)
    if inner is None or bot.status != "online":
        return JSONResponse({"error": f"bot status={bot.status}, send unavailable"},
                            status_code=503)
    # 前端拿到的 user_id 是 uid[:8]+"..." 截断串; 解析回完整 uid
    if to.endswith("...") and hasattr(bot, "connected_users"):
        _prefix = to[:-3]
        _match = [u for u in bot.connected_users if u.startswith(_prefix)]
        if len(_match) == 1:
            to = _match[0]
        elif len(_match) > 1:
            return JSONResponse({"error": f"user_id 前缀 {_prefix!r} 有 {len(_match)} 个匹配, 请给完整 uid"},
                                status_code=400)
    try:
        await inner.send(to, text)
        try:
            ws = pathlib.Path(S.cfg.get("paths", {}).get("workspace_base", "/tmp/litecode_workspace"))
            (ws / "telemetry").mkdir(parents=True, exist_ok=True)
            with (ws / "telemetry" / "wechat.jsonl").open("a", encoding="utf-8") as fp:
                fp.write(json.dumps({
                    "ts": time.time(), "event": "wx_send",
                    "bot_id": bot_id, "to": to,
                    "text_len": len(text), "text_preview": text[:80],
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return {"ok": True, "bot_id": bot_id, "to": to, "text_len": len(text)}
    except Exception as e:
        msg = str(e)
        if "NoContextError" in type(e).__name__ or "context_token" in msg:
            return JSONResponse({
                "error": "微信 SDK 限制: 无法主动给该用户发起对话, 需对方先发消息",
                "detail": msg,
                "hint": "用 reply (回复入站消息) 替代主动 send; 或先让目标用户 @ bot",
            }, status_code=422)
        return JSONResponse({"error": f"{type(e).__name__}: {msg}"}, status_code=500)


@router.get("/api/wechat/qr")
async def api_wechat_qr(request: Request, text: str = "", size: int = 220):
    """本地生成二维码 PNG. text=要编码的文字/URL."""
    require_auth(request)
    if not text:
        raise HTTPException(400, "text required")
    size = max(80, min(512, size))
    try:
        import qrcode
        import io
        box = max(3, size // 30)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        try:
            img = img.resize((size, size))
        except Exception:
            pass
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=120"},
        )
    except ImportError:
        raise HTTPException(503, "qrcode 库未安装 (pip install qrcode[pil])")
    except Exception as e:
        raise HTTPException(500, f"qr generation failed: {e}")
