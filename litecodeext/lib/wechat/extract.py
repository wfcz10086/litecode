"""lib/wechat/extract.py — 微信入站消息内容解析 _extract_message_full（从 wechat_bridge.py 抽取, 纯搬运）。"""

import re
from typing import Optional

from lib.wechat_media import _sdk_download_media
from lib.wechat_attach import _try_whisper_transcribe


# ══════════════════════════════════════════════════════════════
# 消息内容解析（参考 bot.py 的 parse_message_content）
# ══════════════════════════════════════════════════════════════
async def _extract_message_full(msg, bot_id: str = "", bot=None) -> Optional[str]:
    """
    提取消息内容：
    - 文字 → 直接用
    - 图片/文件/语音/视频 → 用 SDK bot.download_raw(media, aes_key) 下载
      ⚠️  微信 CDN 有 encrypt_query_param 加密参数，必须走 SDK，不能裸 HTTP GET
    - 语音 → 尝试 Whisper 转写
    - 转发聊天记录 → 解析 XML 文本
    """
    parts = []

    # ── 主文本 ──
    text = (getattr(msg, "text", "") or "").strip()
    if text:
        parts.append(text)

    # ── 图片 ──
    img_paths = []
    for img in getattr(msg, "images", []):
        local = await _sdk_download_media(
            bot, img, ext="jpg", bot_id=bot_id,
            fallback_url=getattr(img, "url", "") or ""
        )
        if local:
            img_paths.append(local)
        else:
            parts.append("[图片下载失败]")
    if img_paths:
        parts.append("[收到图片]\n" + "\n".join(f"图片路径: {p}" for p in img_paths))

    # ── 语音 ──
    for v in getattr(msg, "voices", []):
        duration = getattr(v, "duration_ms", 0)
        transcript = (getattr(v, "text", "") or "").strip()
        if transcript:
            parts.append(f"[语音 {duration//1000}s 转写]: {transcript}")
            continue
        local = await _sdk_download_media(
            bot, v, ext="silk", bot_id=bot_id,
            fallback_url=getattr(v, "url", "") or ""
        )
        if local:
            whisper_text = _try_whisper_transcribe(local, "")
            if whisper_text:
                parts.append(f"[语音 {duration//1000}s 转写]: {whisper_text}")
            else:
                parts.append(f"[语音已保存: {local}]（{duration//1000}s，转写失败）")
        else:
            parts.append(f"[语音 {duration//1000}s 下载失败]")

    # ── 文件 ──
    for f in getattr(msg, "files", []):
        fname = getattr(f, "file_name", "unknown")
        fsize = getattr(f, "size", 0)
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
        local = await _sdk_download_media(
            bot, f, ext=ext, filename=fname, bot_id=bot_id,
            fallback_url=getattr(f, "url", "") or ""
        )
        sz = f"{fsize//1024}KB" if fsize < 1048576 else f"{fsize//1048576}MB"
        if local:
            parts.append(f"[收到文件: {fname} ({sz})]\n文件路径: {local}")
        else:
            parts.append(f"[文件: {fname} ({sz}) 下载失败]")

    # ── 视频 ──
    for v in getattr(msg, "videos", []):
        local = await _sdk_download_media(
            bot, v, ext="mp4", bot_id=bot_id,
            fallback_url=getattr(v, "url", "") or ""
        )
        if local:
            parts.append(f"[收到视频]\n视频路径: {local}")
        else:
            parts.append("[视频下载失败]")

    # ── 引用 / 转发聊天记录 ──
    quoted = getattr(msg, "quoted_message", None)
    if quoted:
        title   = (getattr(quoted, "title",   "") or "").strip()
        content = (getattr(quoted, "content", "") or "").strip()
        raw_q   = getattr(quoted, "raw", {}) or {}
        record_lines = ["[转发聊天记录]"]
        if title:
            record_lines.append(f"  标题: {title}")
        if content:
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                line = re.sub(r"<img[^/]*/?>",               "[图片]",       line, flags=re.I)
                line = re.sub(r"<video[^/]*/?>",             "[视频]",       line, flags=re.I)
                line = re.sub(r"<voice[^/]*/?>",             "[语音]",       line, flags=re.I)
                line = re.sub(r"<attach[^>]*>(.*?)</attach>", r"[文件:\1]",  line, flags=re.I)
                line = re.sub(r"<[^>]+>", "",                 line)
                line = line.strip()
                if line:
                    record_lines.append(f"  {line}")
        for key in ("appname", "fromusername", "description", "des", "digest"):
            val = (raw_q.get(key, "") or "").strip()
            if val and val not in (title, content):
                record_lines.append(f"  [{key}] {val}")
        record_lines.append("[/转发聊天记录]")
        # 引用/转发的内容出自别人之手 (不是主人本人这次说的话): 套外部内容信封, 其中的要求只当数据
        # (lib/agent/untrusted.py; 主循环的高影响动作闸也据此判定主人原话不含这段)
        _record = "\n".join(record_lines)
        try:
            from lib.agent.untrusted import wrap_inbound_quote
            _record = wrap_inbound_quote(_record, "微信引用/转发的聊天记录(第三方)")
        except Exception:
            pass
        parts.append(_record)

    if not parts:
        return None
    return "\n".join(parts)
