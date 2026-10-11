"""prompt_snapshot.py — 会话级冻结的 session 上下文 (USER.md / TOOLS.md / SOUL.md ...)。

这些文件在每轮结束后会被自动学习改写 (新端口、新文件路径、用户名字), 而它们在
system prompt 里排得很靠前, 改一个字整段 KV 前缀缓存就失效。

做法: 同一段对话里只渲染一次, 之后原样复用; 只有"历史开头变了" (新会话、
历史被裁剪/压缩, 此时缓存本来就断了) 才重新渲染。新学到的事实本轮对话里
已经出现过, 模型不靠 system prompt 也知道。

快照落盘到 <session_dir>/.prompt_snapshot.json, 服务重启后仍复用同一份。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Optional

from .atomic_io import atomic_write_text

_FILE = ".prompt_snapshot.json"
_mem: dict[str, tuple[str, str]] = {}


def history_anchor(history: list, user_message: str = "") -> str:
    """历史第一条消息的指纹; 空历史用本轮用户原话 (下一轮它就是历史第一条)。"""
    first = history[0] if history else {"role": "user", "content": user_message}
    raw = json.dumps({"r": first.get("role"), "c": first.get("content")},
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _disk(session_dir: Optional[Path]) -> Optional[Path]:
    return session_dir / _FILE if session_dir else None


def frozen(session_id: str, anchor: str, render: Callable[[], str],
           session_dir: Optional[Path] = None) -> str:
    """anchor 不变就返回上次渲染的文本, 否则重新渲染并保存。"""
    hit = _mem.get(session_id)
    if hit is None:
        p = _disk(session_dir)
        if p and p.exists():
            try:
                d = json.loads(p.read_text())
                hit = (d["anchor"], d["text"])
                _mem[session_id] = hit
            except (ValueError, KeyError, OSError):
                hit = None
    if hit and hit[0] == anchor:
        return hit[1]
    text = render()
    _mem[session_id] = (anchor, text)
    p = _disk(session_dir)
    if p and p.parent.exists():
        try:
            atomic_write_text(p, json.dumps({"anchor": anchor, "text": text}, ensure_ascii=False))
        except OSError:
            pass
    return text


def forget(session_id: str, session_dir: Optional[Path] = None) -> None:
    _mem.pop(session_id, None)
    p = _disk(session_dir)
    if p and p.exists():
        p.unlink(missing_ok=True)
