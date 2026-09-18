#!/usr/bin/env python3
"""wecom_bridge.py — 企业微信智能机器人长连接桥 (仿 wechat_bridge 结构).

流程:
  1) subscribe bot_id + secret 建 WebSocket
  2) recv loop 收 aibot_msg_callback → 写 store.messages + upsert_contact
  3) 主动 push 支持 text / markdown / file / image (upload 3 步 + send_msg)
  4) 断线自动重连, ping 心跳
  5) 与 wechat_bridge.WeChatBridgeManager 端口对齐 (add/remove/relogin/list/config)

存储: 每 bot 一份 SQLite (wecom_store.WeComStore)
凭证: ~/.wecom/{bot_id}/creds.json = {"bot_id": ..., "secret": ...}
状态: ~/.wecom/bots_state.json = {"bot_ids": [...]}
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger("wecom_bridge")

_BASE = Path(__file__).parent
_WECOM_DIR = Path.home() / ".wecom"
_BOTS_STATE_FILE = _WECOM_DIR / "bots_state.json"
_WSS_URL = "wss://openws.work.weixin.qq.com"
_UPLOAD_CHUNK = 400 * 1024  # 400 KB
_MSG_TIMEOUT = 60           # 普通 cmd (aibot_send_msg/upload_media_*) 默认 60s
_PING_INTERVAL = 25
_RECONNECT_DELAY = 5

try:
    import websocket  # type: ignore  # websocket-client
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False
    log.warning("[wecom_bridge] websocket-client 未安装, 企微桥不可用")

from wecom_store import WeComStore, get_store, drop_store


# ── 配置 ──────────────────────────────────────────────────────
def _load_cfg() -> dict:
    p = _BASE / "config.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _save_cfg(cfg: dict):
    (_BASE / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=4))


def _wc_cfg(cfg: dict = None) -> dict:
    if cfg is None:
        cfg = _load_cfg()
    return cfg.get("wecom", {})


def _bot_creds_path(bot_id: str) -> Path:
    d = _WECOM_DIR / bot_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "creds.json"


def _load_creds(bot_id: str) -> Optional[dict]:
    p = _bot_creds_path(bot_id)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _save_creds(bot_id: str, secret: str, extra: dict = None):
    p = _bot_creds_path(bot_id)
    d = {"bot_id": bot_id, "secret": secret, "saved_at": time.time()}
    if extra:
        d.update(extra)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2))


# ── Bot 实例 ──────────────────────────────────────────────────
class WeComBotInstance:
    """单个企微智能机器人 (bot_id + secret) 长连接会话."""

    def __init__(self, bot_id: str, secret: str, manager: "WeComBridgeManager"):
        self.bot_id = bot_id
        self.secret = secret
        self.manager = manager
        self.store: WeComStore = get_store(bot_id)
        self.status = "init"   # init | connecting | online | offline | error
        self.last_error: str = ""
        self.last_active: float = 0.0
        self._ws: Any = None
        self._pending: Dict[str, Any] = {}
        self._stop = False
        self._loop_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()
        self._loop = asyncio.get_event_loop()

    # ── 生命周期 ─────────────────────────────────────────────
    async def start(self) -> dict:
        """启动长连接. 后台跑 recv/ping loop."""
        if not _SDK_AVAILABLE:
            self.status = "error"
            self.last_error = "websocket-client 未安装"
            return {"ok": False, "error": self.last_error}
        if self.status == "online":
            return {"ok": True, "status": "already_online"}
        self._stop = False
        self._loop_task = asyncio.create_task(self._connect_loop())
        # 等首次 subscribe 结果 (最多 15s)
        t0 = time.time()
        while time.time() - t0 < 15:
            if self.status in ("online", "error"):
                break
            await asyncio.sleep(0.1)
        return {"ok": self.status == "online", "status": self.status,
                "error": self.last_error}

    async def shutdown(self):
        self._stop = True
        for t in (self._recv_task, self._ping_task, self._loop_task):
            if t and not t.done():
                t.cancel()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        self.status = "offline"

    # ── 连接/重连主循环 ─────────────────────────────────────
    async def _connect_loop(self):
        while not self._stop:
            try:
                await self._connect_once()
                if self._stop:
                    break
                log.warning(f"[{self.bot_id}] 连接断开, {_RECONNECT_DELAY}s 后重连")
                self.status = "offline"
                await asyncio.sleep(_RECONNECT_DELAY)
            except Exception as e:
                log.exception(f"[{self.bot_id}] connect loop 异常: {e}")
                self.status = "error"
                self.last_error = f"{type(e).__name__}: {e}"
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _connect_once(self):
        self.status = "connecting"
        # websocket-client 是同步的, 用 to_thread 跑
        self._ws = await asyncio.to_thread(self._sync_connect)
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._ping_task = asyncio.create_task(self._ping_loop())
        # subscribe
        r = await self._send("aibot_subscribe",
                             {"bot_id": self.bot_id, "secret": self.secret})
        if not r or r.get("errcode") != 0:
            self.status = "error"
            self.last_error = f"subscribe 失败: {r}"
            log.error(f"[{self.bot_id}] {self.last_error}")
            return
        self.status = "online"
        self.last_error = ""
        log.info(f"[{self.bot_id}] ✅ 上线")
        # 阻塞直到 recv/ping 任一挂掉
        done, pending = await asyncio.wait(
            [self._recv_task, self._ping_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()

    def _sync_connect(self):
        ws = websocket.WebSocket()  # type: ignore
        ws.connect(_WSS_URL, ping_interval=None)
        return ws

    # ── recv loop ────────────────────────────────────────────
    async def _recv_loop(self):
        while not self._stop:
            try:
                raw = await asyncio.to_thread(self._ws.recv)
            except Exception as e:
                log.warning(f"[{self.bot_id}] recv 断: {e}")
                return
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            self.last_active = time.time()
            cmd = msg.get("cmd")
            rid = (msg.get("headers") or {}).get("req_id") or ""
            if rid and rid in self._pending:
                self._pending[rid] = msg
                continue
            if cmd in ("aibot_msg_callback", "aibot_event_callback"):
                await self._handle_callback(msg)
            # 其它: 流式回复的迟到 ACK, 已 fire-and-forget, 静默丢弃

    async def _ping_loop(self):
        while not self._stop:
            await asyncio.sleep(_PING_INTERVAL)
            if self._stop:
                break
            try:
                await self._send("ping", {}, wait=False,
                                 req_id=f"ping_{int(time.time()*1000)}")
            except Exception:
                pass

    async def _handle_callback(self, msg: dict):
        """收到用户/事件 → 写 store + 触发上层回调."""
        body = msg.get("body") or {}
        cmd = msg.get("cmd")
        # 提取 userid: single 里在 body.from.userid; group 里可能在 chatid
        userid = ""
        chat_type = str(body.get("chattype", "single"))
        frm = body.get("from") or {}
        if isinstance(frm, dict):
            userid = frm.get("userid") or ""
        userid = userid or body.get("userid") or body.get("chatid") or ""
        msgtype = body.get("msgtype", "unknown")
        # 展平预览
        content = ""
        if msgtype == "text":
            content = (body.get("text") or {}).get("content", "")
        elif msgtype == "markdown":
            content = (body.get("markdown") or {}).get("content", "")
        elif msgtype == "image":
            content = "[图片]"
        elif msgtype == "file":
            content = "[文件] " + (body.get("file") or {}).get("filename", "")
        elif msgtype == "voice":
            # WeCom 服务端做 ASR, 转写文本在 body.voice.content
            asr = (body.get("voice") or {}).get("content", "")
            content = f"[语音] {asr}" if asr else "[语音]"
        else:
            content = f"[{msgtype}]"

        if userid:
            self.store.upsert_contact(userid, chat_type, "", "in")
        # [stream v1] 把 headers.req_id 挂到 body 上, 让 on_message 拿去做流式回复
        _hdr_rid = (msg.get("headers") or {}).get("req_id", "")
        if _hdr_rid:
            body["_req_id"] = _hdr_rid
        self.store.add_message("in", userid, msgtype, content, body,
                               msgid=body.get("msgid", ""))
        # 上层 hook — body 里含 response_url + _req_id, on_message 可选:
        #   HTTP POST response_url (15s TTL) / aibot_send_msg WS / aibot_respond_msg 流式
        try:
            if self.manager.on_message:
                await self.manager.on_message(self.bot_id, userid, msgtype,
                                              content, body)
        except Exception as e:
            log.exception(f"[{self.bot_id}] on_message hook 异常: {e}")

    @staticmethod
    async def reply_via_url(response_url: str, markdown: str) -> dict:
        """POST 到消息回调里带的 response_url. 每 URL 单次使用, 短 TTL."""
        try:
            import aiohttp
        except ImportError:
            return {"ok": False, "errmsg": "aiohttp missing"}
        payload = {"msgtype": "markdown",
                   "markdown": {"content": markdown[:4000]}}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(response_url, json=payload) as r:
                try:
                    data = await r.json()
                except Exception:
                    data = {"errcode": r.status, "errmsg": await r.text()}
        return {"ok": data.get("errcode") == 0, **data}

    # ── 底层 send (等 req_id 响应) ────────────────────────────
    async def _send(self, cmd: str, body: dict, wait: bool = True,
                    timeout: int = _MSG_TIMEOUT,
                    req_id: Optional[str] = None) -> Optional[dict]:
        if self._ws is None:
            return None
        # 流式回复要复用收到的 req_id 做关联, 其它场景生成新的
        req_id = req_id or uuid.uuid4().hex[:16]
        payload = {"cmd": cmd, "headers": {"req_id": req_id}, "body": body}
        if wait:
            self._pending[req_id] = None
        async with self._send_lock:
            try:
                await asyncio.to_thread(self._ws.send, json.dumps(payload))
            except Exception as e:
                self._pending.pop(req_id, None)
                log.warning(f"[{self.bot_id}] send 异常: {e}")
                return None
        if not wait:
            return None
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._pending.get(req_id) is not None:
                return self._pending.pop(req_id)
            await asyncio.sleep(0.05)
        self._pending.pop(req_id, None)
        return None

    # ── 主动发消息 ───────────────────────────────────────────
    async def send_text(self, userid: str, text: str,
                        chat_type: int = 1) -> dict:
        """企微 aibot_send_msg 不吃 msgtype=text, 用 markdown 兜底."""
        return await self.send_markdown(userid, text, chat_type)

    async def send_markdown(self, userid: str, md: str,
                            chat_type: int = 1) -> dict:
        allowed, why = self.store.check_and_incr_rate(userid)
        if not allowed:
            return {"ok": False, "errcode": -1, "errmsg": f"rate_limit: {why}"}
        r = await self._send("aibot_send_msg", {
            "chatid": userid, "chat_type": chat_type,
            "msgtype": "markdown",
            "markdown": {"content": md[:4000]},
        })
        errcode = (r or {}).get("errcode", -1)
        errmsg = (r or {}).get("errmsg", "no response")
        self.store.upsert_contact(userid, direction="out")
        self.store.add_message("out", userid, "markdown", md, {"markdown": md},
                               errcode=errcode, errmsg=errmsg)
        return {"ok": errcode == 0, "errcode": errcode, "errmsg": errmsg}

    async def send_markdown_stream(self, req_id: str, stream_id: str,
                                   content: str, finish: bool,
                                   userid: str = "",
                                   chat_type: int = 1) -> dict:
        """流式回复 (aibot_respond_msg).

        - req_id: 收到消息时 headers.req_id, 用来把回复关联到原消息
        - stream_id: 单次回复的唯一 ID, 首帧到末帧保持一致
        - content: 累计的 Markdown 全文 (每次替换), ≤20480 字节
        - finish: True 表示末帧, 之后 6 分钟窗口关闭
        - userid: 仅用于计费/日志/写 store, 不进 body

        WS 从首帧计 6 分钟 (若 5 分钟没进展会自动兜底 aibot_send_msg).
        """
        if finish and userid:
            allowed, why = self.store.check_and_incr_rate(userid)
            if not allowed:
                return {"ok": False, "errcode": -1,
                        "errmsg": f"rate_limit: {why}"}
        body = {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "content": content[:20000],  # 留 480B 余量给 UTF-8 边界
                "finish": bool(finish),
            },
        }
        # WeCom 服务端 ACK 常 >5s 到达 (SDK 5s timeout 会假阴), 但推流本身瞬时投递.
        # 走 fire-and-forget: 只要 ws.send 不抛就算投递成功, 后到的 ACK 由 _recv_loop
        # 静默丢弃 (不再由 _pending 匹配触发 fallback).
        if self._ws is None:
            errcode, errmsg = -1, "ws not connected"
        else:
            payload = {"cmd": "aibot_respond_msg",
                       "headers": {"req_id": req_id}, "body": body}
            async with self._send_lock:
                try:
                    await asyncio.to_thread(self._ws.send, json.dumps(payload))
                    errcode, errmsg = 0, "sent"
                except Exception as e:
                    log.warning(f"[{self.bot_id}] stream send 异常: {e}")
                    errcode, errmsg = -1, f"ws send failed: {e}"
        # 只在末帧写 store, 避免每 chunk 一行淹没
        if finish and userid:
            self.store.upsert_contact(userid, direction="out")
            self.store.add_message("out", userid, "markdown", content,
                                   {"markdown": content, "via": "stream",
                                    "stream_id": stream_id},
                                   errcode=errcode, errmsg=errmsg)
        return {"ok": errcode == 0, "errcode": errcode, "errmsg": errmsg}

    async def send_file(self, userid: str, local_path: str,
                        chat_type: int = 1) -> dict:
        media_id = await self.upload_media(local_path, "file")
        if not media_id:
            return {"ok": False, "errcode": -1, "errmsg": "upload failed"}
        allowed, why = self.store.check_and_incr_rate(userid)
        if not allowed:
            return {"ok": False, "errcode": -1, "errmsg": f"rate_limit: {why}"}
        r = await self._send("aibot_send_msg", {
            "chatid": userid, "chat_type": chat_type,
            "msgtype": "file", "file": {"media_id": media_id},
        })
        errcode = (r or {}).get("errcode", -1)
        errmsg = (r or {}).get("errmsg", "no response")
        self.store.upsert_contact(userid, direction="out")
        self.store.add_message("out", userid, "file",
                               f"[文件] {os.path.basename(local_path)}",
                               {"file": {"media_id": media_id, "path": local_path}},
                               media_id=media_id,
                               errcode=errcode, errmsg=errmsg)
        return {"ok": errcode == 0, "errcode": errcode, "errmsg": errmsg,
                "media_id": media_id}

    async def send_image(self, userid: str, local_path: str,
                         chat_type: int = 1) -> dict:
        media_id = await self.upload_media(local_path, "image")
        if not media_id:
            return {"ok": False, "errcode": -1, "errmsg": "upload failed"}
        allowed, why = self.store.check_and_incr_rate(userid)
        if not allowed:
            return {"ok": False, "errcode": -1, "errmsg": f"rate_limit: {why}"}
        r = await self._send("aibot_send_msg", {
            "chatid": userid, "chat_type": chat_type,
            "msgtype": "image", "image": {"media_id": media_id},
        })
        errcode = (r or {}).get("errcode", -1)
        errmsg = (r or {}).get("errmsg", "no response")
        self.store.upsert_contact(userid, direction="out")
        self.store.add_message("out", userid, "image",
                               f"[图片] {os.path.basename(local_path)}",
                               {"image": {"media_id": media_id, "path": local_path}},
                               media_id=media_id,
                               errcode=errcode, errmsg=errmsg)
        return {"ok": errcode == 0, "errcode": errcode, "errmsg": errmsg,
                "media_id": media_id}

    # ── 3 步分片上传 ─────────────────────────────────────────
    async def upload_media(self, local_path: str, mtype: str = "file") -> str:
        if not os.path.exists(local_path):
            log.warning(f"[{self.bot_id}] upload 文件不存在: {local_path}")
            return ""
        data = await asyncio.to_thread(lambda: open(local_path, "rb").read())
        total_size = len(data)
        md5 = hashlib.md5(data).hexdigest()
        # 命中 3 天缓存直接复用
        cached = self.store.find_media_by_md5(md5)
        if cached:
            return cached["media_id"]
        chunks = [data[i:i + _UPLOAD_CHUNK]
                  for i in range(0, total_size, _UPLOAD_CHUNK)] or [b""]
        fname = os.path.basename(local_path)

        r = await self._send("aibot_upload_media_init", {
            "type": mtype, "filename": fname, "total_size": total_size,
            "total_chunks": len(chunks), "md5": md5,
        })
        if not r or r.get("errcode") != 0:
            log.warning(f"[{self.bot_id}] upload init 失败: {r}")
            return ""
        upload_id = (r.get("body") or {}).get("upload_id")
        if not upload_id:
            return ""
        for idx, chunk in enumerate(chunks):
            r = await self._send("aibot_upload_media_chunk", {
                "upload_id": upload_id, "chunk_index": idx,
                "base64_data": base64.b64encode(chunk).decode(),
            })
            if not r or r.get("errcode") != 0:
                log.warning(f"[{self.bot_id}] chunk {idx} 失败: {r}")
                return ""
        r = await self._send("aibot_upload_media_finish", {"upload_id": upload_id})
        if not r or r.get("errcode") != 0:
            return ""
        media_id = (r.get("body") or {}).get("media_id", "")
        if media_id:
            self.store.add_media(media_id, fname, mtype, total_size, md5, local_path)
        return media_id

    # ── info / 面板用 ────────────────────────────────────────
    def info(self) -> dict:
        st = self.store.stats()
        return {
            "bot_id": self.bot_id,
            "status": self.status,
            "last_error": self.last_error,
            "last_active": self.last_active,
            "contacts": st["contacts"],
            "messages": st["messages"],
            "media_valid": st["media_valid"],
        }


# ── Manager ──────────────────────────────────────────────────
OnMessageCb = Callable[[str, str, str, str, dict], Awaitable[None]]


class WeComBridgeManager:
    def __init__(self):
        self.bots: Dict[str, WeComBotInstance] = {}
        self.on_message: Optional[OnMessageCb] = None
        self._started = False

    # ── 状态持久化 ───────────────────────────────────────────
    def _save_state(self):
        try:
            _WECOM_DIR.mkdir(parents=True, exist_ok=True)
            _BOTS_STATE_FILE.write_text(json.dumps(
                {"bot_ids": list(self.bots.keys()), "saved_at": time.time()},
                ensure_ascii=False, indent=2))
        except Exception as e:
            log.warning(f"[wecom_bridge] 保存状态失败: {e}")

    def _load_state(self) -> List[str]:
        if _BOTS_STATE_FILE.exists():
            try:
                d = json.loads(_BOTS_STATE_FILE.read_text())
                return list(d.get("bot_ids") or [])
            except Exception:
                pass
        # 兜底: 扫描 ~/.wecom/*/creds.json
        found = []
        if _WECOM_DIR.exists():
            for creds in _WECOM_DIR.glob("*/creds.json"):
                bid = creds.parent.name
                if bid:
                    found.append(bid)
        return found

    # ── 生命周期 ─────────────────────────────────────────────
    async def startup(self):
        if self._started:
            return
        self._started = True
        if not _SDK_AVAILABLE:
            log.warning("[wecom_bridge] websocket-client 未装, 跳过")
            return
        cfg = _wc_cfg()
        if not cfg.get("enabled", False):
            log.info("[wecom_bridge] 未启用 (config.wecom.enabled=false)")
            return
        bot_ids = self._load_state()
        if not bot_ids:
            log.info("[wecom_bridge] 无保存的 bot, 等待添加")
            return
        for bid in bot_ids:
            creds = _load_creds(bid)
            if not creds or not creds.get("secret"):
                log.warning(f"[wecom_bridge] {bid} 无凭证, 跳过")
                continue
            try:
                bot = WeComBotInstance(bid, creds["secret"], self)
                self.bots[bid] = bot
                asyncio.create_task(bot.start())
                log.info(f"[wecom_bridge] ✅ 恢复 {bid}")
            except Exception as e:
                log.error(f"[wecom_bridge] 恢复 {bid} 失败: {e}")

    async def shutdown_all(self):
        for bot in list(self.bots.values()):
            await bot.shutdown()

    async def add_bot(self, bot_id: str, secret: str) -> dict:
        if not _SDK_AVAILABLE:
            return {"ok": False, "error": "websocket-client 未装"}
        if not bot_id or not secret:
            return {"ok": False, "error": "bot_id/secret 必填"}
        if bot_id in self.bots:
            bot = self.bots[bot_id]
            if bot.status == "online":
                return {"ok": True, "bot_id": bot_id, "status": "already_online"}
            r = await bot.start()
            return {"bot_id": bot_id, **r}
        _save_creds(bot_id, secret)
        bot = WeComBotInstance(bot_id, secret, self)
        self.bots[bot_id] = bot
        r = await bot.start()
        self._save_state()
        return {"bot_id": bot_id, **r}

    async def remove_bot(self, bot_id: str) -> dict:
        bot = self.bots.pop(bot_id, None)
        if bot:
            await bot.shutdown()
            drop_store(bot_id)
            self._save_state()
            return {"ok": True}
        return {"ok": False, "error": "not found"}

    async def relogin(self, bot_id: str) -> dict:
        bot = self.bots.get(bot_id)
        if not bot:
            creds = _load_creds(bot_id)
            if not creds:
                return {"ok": False, "error": "no creds"}
            return await self.add_bot(bot_id, creds["secret"])
        await bot.shutdown()
        r = await bot.start()
        return {"bot_id": bot_id, **r}

    def list_bots(self) -> List[dict]:
        return [bot.info() for bot in self.bots.values()]

    def get_config(self) -> dict:
        cfg = _wc_cfg()
        return {
            "enabled": cfg.get("enabled", False),
            "sdk_available": _SDK_AVAILABLE,
            "bot_count": len(self.bots),
        }

    def update_config(self, updates: dict) -> dict:
        cfg = _load_cfg()
        wc = cfg.setdefault("wecom", {})
        if "enabled" in updates:
            wc["enabled"] = bool(updates["enabled"])
        _save_cfg(cfg)
        return self.get_config()

    async def send(self, bot_id: str, userid: str, text: str = "",
                   markdown: str = "", file_path: str = "",
                   image_path: str = "", chat_type: int = 1) -> dict:
        """timer / router 共用入口."""
        bot = self.bots.get(bot_id)
        if not bot:
            return {"ok": False, "error": f"bot {bot_id} not found"}
        if bot.status != "online":
            return {"ok": False, "error": f"bot status={bot.status}"}
        if image_path:
            return await bot.send_image(userid, image_path, chat_type)
        if file_path:
            return await bot.send_file(userid, file_path, chat_type)
        if markdown:
            return await bot.send_markdown(userid, markdown, chat_type)
        if text:
            return await bot.send_text(userid, text, chat_type)
        return {"ok": False, "error": "empty payload"}


_manager: Optional[WeComBridgeManager] = None


def get_manager() -> WeComBridgeManager:
    global _manager
    if _manager is None:
        _manager = WeComBridgeManager()
    return _manager
