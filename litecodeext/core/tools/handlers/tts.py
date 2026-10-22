"""handlers/tts.py — text_to_speech: 文字转语音, 成品存会话产物目录 (实现见 core/tts.py)。"""
from __future__ import annotations

import time
from pathlib import Path

from . import register


def _artifact_dir(sid: str | None) -> Path:
    from lib.config import SESSIONS_DISK
    return Path(SESSIONS_DISK) / (sid or "_orphan") / "artifacts"


@register("text_to_speech")
async def h_text_to_speech(sid: str, args: dict):
    from core import tts
    action = str(args.get("action") or "speak").strip().lower()
    if action in ("voices", "list", "list_voices"):
        return tts.voices_text(), None
    try:
        r = await tts.synthesize(
            args.get("text", ""), _artifact_dir(sid),
            voice=args.get("voice"), engine=str(args.get("engine") or "auto"),
            rate=args.get("rate"), pitch=args.get("pitch"), volume=args.get("volume"),
            fmt=str(args.get("format") or "mp3"),
        )
    except ValueError as e:
        return f"ERROR: text_to_speech {e}", None
    except Exception as e:
        return f"ERROR: text_to_speech {type(e).__name__}: {e}", None
    path = r["path"]
    try:
        import tool_dispatch   # 扁平名: 和 litecode_server 注入 _add_artifact_fn 的是同一个模块实例
        tool_dispatch._add_artifact(sid, {"filepath": path, "op": "text_to_speech", "kind": Path(path).suffix.lstrip("."),
                                          "size": Path(path).stat().st_size, "ts": time.time()})
    except Exception:
        pass
    voice_cn = (tts.EDGE_VOICES.get(r["voice"]) or tts.DS_VOICES.get(r["voice"]) or (r["voice"],))[0]
    eng_cn = tts.ENGINE_NAMES.get(r["engine"], r["engine"])
    lines = [f"[text_to_speech OK] {path}",
             f"  时长 {r['duration']} 秒 · {r['chars']} 字 · {r['chunks']} 段 · 引擎 {eng_cn} · 音色 {voice_cn} ({r['voice']})"]
    if r.get("note"):
        lines.append(f"  提示: {r['note']}")
    lines.append(f"回复里写出完整路径 {path} (网页据此显示播放器和下载按钮); "
                 f"微信/企微会话再加一行 [发送文件: {path}] 才会把音频发过去。")
    return "\n".join(lines), None
