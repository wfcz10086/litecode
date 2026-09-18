"""wecom_chat_bridge.py — WeCom 消息接入 (从 web_ui.py:_lifespan 抽出).

**拆分动机 (2026-07-26)**: web_ui.py 突破 1000 行硬顶 (1314 行),
把 `_wc_on_msg` (~150 行) + `_wc_send` (~7 行) 提出来独立成文件,
web_ui.py 端只保留 factory 调用. 顺便修好用户发图被丢的 bug —
现在 msgtype='image' 会构造 OpenAI 多模态 content 走 transport
的 vision fallback (Qwen3.6-35B).

**导出**:
  - `make_wc_on_msg(get_wcmgr, cfg)` → async handler 挂 wcmgr.on_message
  - `make_wc_sender(get_wcmgr)` → timer.set_wecom_sender 用
"""
from __future__ import annotations
import asyncio
import base64
import json
import time
from typing import Callable

# 5 秒防抖: 快回复不打扰, 慢回复才占位 (参考 wechat_bridge typing 心跳风格)
_THINK_DELAY_SEC = 5.0
_THINK_TEXT = "🤔 正在思考中…请稍候 (回复到了会再发一条)"


_IMG_MAGIC = [
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),  # WEBP 头是 RIFF....WEBP, 简化只看 RIFF
    (b"BM", "bmp"),
]


def _sniff_image_mime(data: bytes) -> str:
    for magic, fmt in _IMG_MAGIC:
        if data.startswith(magic):
            return f"image/{fmt}"
    return "image/jpeg"  # 兜底 (vLLM 一般不严格校验)


def _decrypt_wecom_aibot_file(encrypted: bytes, aeskey: str) -> bytes:
    """企微智能机器人 AES-256-CBC 解密.

    对齐官方 SDK (WecomTeam/wecom-aibot-python-sdk aibot/crypto_utils.py):
      - key  = base64_decode(aeskey + '='*补齐), 32 bytes
      - iv   = key[:16]
      - 尾部不是 16 倍数补 \\x00 (Node SDK setAutoPadding(false) 语义)
      - PKCS#7 unpad, pad_len 支持 1..32
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    padded = aeskey + "=" * (4 - len(aeskey) % 4) if len(aeskey) % 4 else aeskey
    key = base64.b64decode(padded)
    iv = key[:16]
    block = 16
    rem = len(encrypted) % block
    if rem:
        encrypted = encrypted + b"\x00" * (block - rem)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    pt = dec.update(encrypted) + dec.finalize()
    if not pt:
        raise ValueError("空明文")
    pad_len = pt[-1]
    if 1 <= pad_len <= 32 and all(b == pad_len for b in pt[-pad_len:]):
        pt = pt[:-pad_len]
    return pt


async def _download_wc_image_as_data_url(url: str, aeskey: str,
                                          timeout_sec: int = 60) -> str:
    """企微 aibot image URL → 下载 + AES-256-CBC 解密 → data:image/*;base64,..."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.get(url) as r:
            r.raise_for_status()
            raw = await r.read()
    if aeskey:
        try:
            raw = _decrypt_wecom_aibot_file(raw, aeskey)
        except Exception as _ex:
            print(f"[wc_on_msg] AES-256-CBC 解密失败 (用原始 bytes 兜底): {_ex}")
    mime = _sniff_image_mime(raw)
    print(f"[wc_on_msg][IMG-OK] len={len(raw)} mime={mime} head={raw[:8].hex()}")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def make_wc_on_msg(get_wcmgr: Callable, cfg: dict) -> Callable:
    """构造 WeCom on_message handler. 走 aibot_respond_msg 流式回复.

    6 分钟窗口 (WS 从首帧计), 中途每 ~0.8s flush 一次累计文本.
    首帧失败/agent 卡住 → 兜底 response_url / aibot_send_msg.
    """
    async def _wc_on_msg(bot_id: str, userid: str, msgtype: str,
                          content: str, body: dict):
        if not userid:
            return
        response_url = body.get("response_url", "")
        req_id_in = body.get("_req_id", "")
        # [2026-07-26] image 走 LLM (原来直接 return 丢图);
        # OpenAI 多模态 content list, transport 自动路由 vision fallback (Qwen3.6-35B)
        vision_content = None
        if msgtype == "voice":
            user_msg = (body.get("voice") or {}).get("content", "").strip()
        elif msgtype in ("text", "markdown"):
            user_msg = (content or "").strip()
        elif msgtype == "image":
            img_obj = body.get("image") or {}
            img_url = (img_obj.get("url") or "").strip()
            aeskey = (img_obj.get("aeskey") or "").strip()
            if not img_url:
                return
            user_msg = "这张图片里的内容是什么? 请描述关键信息."
            # 企微 image URL 拉回是 AES-128-ECB 加密二进制 (aeskey 在同层),
            # 直接给 qwen3.6 会 400 'cannot identify image file'.
            # 服务端下载 + 解密 + 转 base64 data URL → vLLM 才认.
            try:
                data_url = await _download_wc_image_as_data_url(img_url, aeskey)
                image_ref = data_url
            except Exception as _ex:
                print(f"[wc_on_msg] 图片下载/解密失败, 回退原 URL: {_ex}")
                image_ref = img_url
            vision_content = [
                {"type": "text", "text": user_msg},
                {"type": "image_url", "image_url": {"url": image_ref}},
            ]
        else:
            return  # file/其它 暂不自动回复
        if not user_msg and not vision_content:
            return

        from wecom_bridge import WeComBotInstance as _WCBI  # type: ignore
        _wcmgr = get_wcmgr()
        bot = _wcmgr.bots.get(bot_id)

        # ── "正在思考"占位 (5s 防抖) ─────────────────────────
        # 若 agent 在 5s 内出结果 → 定时器被 cancel, 用户看不到占位;
        # 慢回复 → 先发一条 "🤔 正在思考中…", 正式回复到了再单独发一条
        # (企微不支持撤回, 两条共存). 参考 wechat_bridge.py:2100 typing 心跳.
        _think_state = {"sent": False, "cancelled": False}

        async def _send_thinking_placeholder():
            try:
                await asyncio.sleep(_THINK_DELAY_SEC)
            except asyncio.CancelledError:
                return
            if _think_state["cancelled"] or _think_state["sent"]:
                return
            _think_state["sent"] = True
            try:
                if response_url:
                    await _WCBI.reply_via_url(response_url, _THINK_TEXT)
                else:
                    await _wcmgr.send(bot_id, userid, markdown=_THINK_TEXT)
                print(f"[wc_on_msg] {bot_id}/{userid} 已发占位 (>{_THINK_DELAY_SEC}s)")
            except Exception as _ex:
                print(f"[wc_on_msg] 占位发送失败 (忽略): {_ex}")

        _think_task = asyncio.create_task(_send_thinking_placeholder())

        def _cancel_thinking():
            """正式回复即将/已经发出 → 立即停占位任务."""
            _think_state["cancelled"] = True
            if not _think_task.done():
                _think_task.cancel()

        async def _fallback_reply(md: str):
            """流式失败/不可用时兜底: response_url 快通道 → WS aibot_send_msg."""
            _cancel_thinking()
            md = (md or "").strip() or "_(空回复)_"
            if response_url:
                r = await _WCBI.reply_via_url(response_url, md[:4000])
                if bot:
                    bot.store.add_message(
                        "out", userid, "markdown", md,
                        {"markdown": md, "via": "response_url"},
                        errcode=r.get("errcode", -1),
                        errmsg=r.get("errmsg", ""))
                    bot.store.upsert_contact(userid, direction="out")
                if r.get("errcode") == 0:
                    return
            await _wcmgr.send(bot_id, userid, markdown=md[:4000])

        # 无 req_id 说明底层没挂上, 或消息结构变了 → 老路径
        if not req_id_in or not bot:
            try:
                import aiohttp
                srv = cfg.get("server", {})
                url = f"http://127.0.0.1:{srv.get('port', 18789)}/v1/chat/completions"
                payload = {
                    "model": cfg.get("model", {}).get("id", "openclaw"),
                    "stream": False,
                    "messages": [{"role": "user",
                                  "content": vision_content or user_msg}],
                    "user": f"wc-{bot_id}-{userid}",
                }
                hdrs = {"Authorization": f"Bearer {srv.get('token','')}",
                        "Content-Type": "application/json"}
                timeout = aiohttp.ClientTimeout(total=300)
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.post(url, json=payload, headers=hdrs) as r:
                        data = await r.json()
                reply = data["choices"][0]["message"]["content"]
                await _fallback_reply(reply)
            except Exception as _ex:
                print(f"[wc_on_msg] fallback 失败: {_ex}")
                try:
                    await _fallback_reply(f"⚠️ 处理失败: `{_ex}`")
                except Exception:
                    pass
            return

        # ── 流式主路径 ──
        import aiohttp
        import uuid as _uuid
        stream_id = _uuid.uuid4().hex[:16]
        srv = cfg.get("server", {})
        url = f"http://127.0.0.1:{srv.get('port', 18789)}/v1/chat/completions"
        payload = {
            "model": cfg.get("model", {}).get("id", "openclaw"),
            "stream": True,
            "messages": [{"role": "user",
                          "content": vision_content or user_msg}],
            "user": f"wc-{bot_id}-{userid}",
        }
        hdrs = {"Authorization": f"Bearer {srv.get('token','')}",
                "Content-Type": "application/json"}
        acc = ""
        last_flush_ts = 0.0
        last_flushed_len = 0
        FLUSH_INTERVAL = 0.8   # 秒
        FLUSH_DELTA = 40       # 累计增量字符
        stream_started = False

        async def _flush(final: bool):
            nonlocal last_flush_ts, last_flushed_len, stream_started
            if not acc and not final:
                return
            _cancel_thinking()  # 首次真回复出手 → 取消占位
            body_txt = acc or "_(空回复)_"
            r = await bot.send_markdown_stream(
                req_id=req_id_in, stream_id=stream_id,
                content=body_txt, finish=final, userid=userid)
            last_flush_ts = time.time()
            last_flushed_len = len(acc)
            if r.get("ok"):
                stream_started = True
            return r

        try:
            timeout = aiohttp.ClientTimeout(total=360)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(url, json=payload, headers=hdrs) as r:
                    # aiohttp StreamReader 迭代粒度不保证按行, 手动 readline
                    while True:
                        raw = await r.content.readline()
                        if not raw:
                            break
                        s = raw.decode("utf-8", errors="ignore").strip()
                        if not s.startswith("data:"):
                            continue
                        data_s = s[5:].strip()
                        if data_s == "[DONE]":
                            break
                        try:
                            d = json.loads(data_s)
                            delta = d["choices"][0]["delta"].get("content") or ""
                        except Exception:
                            delta = ""
                        if not delta:
                            continue
                        acc += delta
                        now = time.time()
                        if (now - last_flush_ts >= FLUSH_INTERVAL
                                and len(acc) - last_flushed_len >= FLUSH_DELTA):
                            await _flush(final=False)
            # 末帧
            fr = await _flush(final=True)
            print(f"[wc_on_msg] {bot_id}/{userid} stream done "
                  f"len={len(acc)} started={stream_started} final={fr}")
            # 首帧都没发出去 → 兜底
            if not stream_started and acc:
                await _fallback_reply(acc)
        except Exception as _ex:
            import traceback as _tb
            print(f"[wc_on_msg] {bot_id}/{userid} 流式失败: {_ex}\n{_tb.format_exc()}")
            # 累计到的先发出去, 否则报错
            try:
                await _fallback_reply(acc or f"⚠️ 处理失败: `{_ex}`")
            except Exception:
                pass
        finally:
            # 兜底: 任何走向 handler 结束前保底 cancel 占位任务 (幂等)
            _cancel_thinking()

    return _wc_on_msg


def make_wc_sender(get_wcmgr: Callable) -> Callable:
    """构造 timer 用的 WeCom sender: async fn(bot_id, userid, text, mode='markdown')."""
    async def _wc_send(bot_id: str, user_id: str, text: str,
                       mode: str = "markdown"):
        wcmgr = get_wcmgr()
        if mode == "text":
            return await wcmgr.send(bot_id, user_id, text=text)
        return await wcmgr.send(bot_id, user_id, markdown=text)
    return _wc_send
