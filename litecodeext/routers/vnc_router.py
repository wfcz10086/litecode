"""vnc_router.py — 登录后才能用的远程桌面 (noVNC) 同源反向代理.

[sec 2026-10-19] 以前 x11vnc 用 -nopw、websockify 监听 0.0.0.0:${NOVNC_PORT:-18800}, 任何人
打开 http://<host>:18800/vnc.html 就能看到并操作容器桌面 (浏览器里的登录态、终端…)。
现在 x11vnc 加 -localhost、websockify 只绑 127.0.0.1 (entrypoint.sh, 容器重启后生效),
用户通过 Web UI 同源入口访问:

    GET  /vnc/                 → 跳到 vnc.html (带 path=vnc/websockify 等参数)
    GET  /vnc/{file}           → 只读托管 $NOVNC_WEB_DIR (默认 /opt/noVNC) 下的 noVNC 前端文件
    GET  /vnc/defaults.json    → noVNC 默认设置 (path 指向本代理), 不依赖查询参数
    WS   /vnc/websockify       → 校验登录 cookie + 同源 Origin 后, 双向转发到 127.0.0.1:${NOVNC_PORT}
    WS   /websockify           → 同上 (noVNC 设置里填了 host 时 path 按根路径解析)

全部要求 Web UI 登录 (cookie lc_auth); WebSocket 额外校验 Origin 与 Host 一致, 防跨站 WS 劫持
(同一主机其它端口上的页面与本站同 site, SameSite=Lax cookie 挡不住)。
上游用 127.0.0.1, 对现在仍绑 0.0.0.0 的 websockify 与重启后只绑 127.0.0.1 的都能用。
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

router = APIRouter()
ORDER = 70

log = logging.getLogger("vnc_router")

# path 相对页面 (/vnc/vnc.html) 解析 → /vnc/websockify; noVNC 设置里填了 host 时按根路径解析 → /websockify (下方同样代理)
_VNC_QUERY = "autoconnect=true&resize=scale&quality=6&compression=2&path=websockify"
_DEFAULTS = {"path": "websockify", "autoconnect": True, "resize": "scale",
             "quality": 6, "compression": 2, "reconnect": True}


def _web_dir() -> Path:
    return Path(os.environ.get("NOVNC_WEB_DIR", "/opt/noVNC"))


def _upstream_url() -> str:
    port = int(os.environ.get("NOVNC_PORT", "18800"))
    return f"ws://127.0.0.1:{port}/websockify"


def _authed(conn) -> bool:
    from plugins.auth import core as auth
    return bool(auth._check_token(auth._get_cookie(conn)))


def _same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:            # 非浏览器客户端 (无 Origin) 只靠 cookie
        return True
    host = (ws.headers.get("host") or "").lower()
    try:
        return urlsplit(origin).netloc.lower() == host
    except ValueError:
        return False


@router.get("/vnc")
async def vnc_root_noslash():
    return RedirectResponse("/vnc/", status_code=307)


@router.get("/vnc/")
async def vnc_index(request: Request):
    if not _authed(request):
        return RedirectResponse("/", status_code=303)       # 先登录
    return RedirectResponse(f"/vnc/vnc.html?{_VNC_QUERY}", status_code=307)


@router.get("/vnc/defaults.json")
async def vnc_defaults(request: Request):
    if not _authed(request):
        raise HTTPException(401, "Unauthorized")
    return JSONResponse(_DEFAULTS, headers={"Cache-Control": "no-store"})


@router.get("/vnc/{path:path}")
async def vnc_static(path: str, request: Request):
    if not _authed(request):
        if path.endswith(".html"):
            return RedirectResponse("/", status_code=303)
        raise HTTPException(401, "Unauthorized")
    root = _web_dir().resolve()
    if not root.is_dir():
        raise HTTPException(503, f"noVNC 未安装 ({root})")
    fp = (root / (path or "vnc.html")).resolve()
    try:
        rel = fp.relative_to(root)
    except ValueError:
        raise HTTPException(404)
    if any(p.startswith(".") for p in rel.parts) or not fp.is_file():
        raise HTTPException(404)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
    media = None
    if fp.suffix in (".js", ".mjs"):
        media = "text/javascript; charset=utf-8"
    return FileResponse(fp, media_type=media, headers=headers)


@router.websocket("/vnc/websockify")
@router.websocket("/websockify")
async def vnc_ws(ws: WebSocket):
    if not _authed(ws) or not _same_origin(ws):
        await ws.close(code=1008)          # 握手阶段拒绝 → 客户端收到 403
        return
    import websockets
    requested = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    try:
        upstream = await websockets.connect(
            _upstream_url(), subprotocols=requested or None, max_size=None,
            open_timeout=5, ping_interval=None, compression=None)
    except Exception as e:
        log.warning("noVNC upstream 连接失败: %s", e)
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=upstream.subprotocol)

    async def client_to_up():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])
            elif msg.get("text") is not None:
                await upstream.send(msg["text"])

    async def up_to_client():
        async for data in upstream:
            if isinstance(data, (bytes, bytearray)):
                await ws.send_bytes(bytes(data))
            else:
                await ws.send_text(data)

    tasks = [asyncio.create_task(client_to_up()), asyncio.create_task(up_to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # 收尾不在可能已被取消的当前任务里 await (否则与服务器关停/客户端断开的取消互相打架):
        # 取消两个转发任务, 上游关闭丢给独立任务异步完成
        for t in tasks:
            t.cancel()
        _bg = asyncio.create_task(upstream.close())
        _CLOSERS.add(_bg)
        _bg.add_done_callback(_CLOSERS.discard)
    try:
        await ws.close()
    except Exception:
        pass


_CLOSERS: set = set()   # 持有后台关闭任务的引用, 防止被 GC 提前回收
