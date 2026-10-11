"""handlers/browser_ref.py — browser_snapshot / browser_act (编号式浏览器操作)。

每个 LiteCode 会话用独立的浏览器 session (lc-<sid>), 页面状态跨调用保留;
action=close 立即释放, 否则由 browser_server 的空闲超时回收。
"""
from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any

from lib.config import WORKSPACE
from tools.browser_ref import SNAPSHOT_JS, build_action, format_snapshot, parse_snapshot

from . import register

try:
    import browser_search as _bs
except ImportError:
    _bs = None

_TIMEOUT = 45


def _session(sid: str) -> str:
    return f"lc-{(sid or 'default')[:12]}"


def _post(action: str, params: dict, session: str, wait_ms: int = 0, shot: bool = False) -> dict:
    return _bs._post_act(action, params, session=session, wait_ms=wait_ms, want_shot=shot)


def _save_shot(b64: str | None) -> str:
    if not b64:
        return ""
    d = Path(WORKSPACE) / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"snap_{int(time.time() * 1000)}.png"
    p.write_bytes(base64.b64decode(b64))
    return str(p)


def _snapshot(session: str, shot: bool) -> str:
    r = _post("eval", {"script": SNAPSHOT_JS}, session, shot=shot)
    if not r.get("ok"):
        return f"ERROR: 快照失败: {r.get('msg', '')}"
    text = format_snapshot(parse_snapshot(r.get("data")))
    path = _save_shot(r.get("b64")) if shot else ""
    return text + (f"\n截图: {path}" if path else "")


def _act(session: str, args: dict) -> str:
    action = (args.get("action") or "").strip()
    built = build_action(action, args.get("ref"), args.get("value"))
    if isinstance(built, str):
        return built
    srv, params = built
    if "sel" in params:
        probe = _post("eval", {"script": f"!!document.querySelector('{params['sel']}')"}, session)
        if probe.get("data") is not True:
            return f"ERROR: 编号 [{args.get('ref')}] 在当前页面不存在 (页面已变化), 先重新 browser_snapshot"
    r = _post(srv, params, session, wait_ms=int(args.get("wait_ms") or 800))
    head = ("✅ " if r.get("ok") else "❌ ") + (r.get("msg") or action)
    if not r.get("ok"):
        return f"ERROR: {head}"
    return head + "\n\n" + _snapshot(session, bool(args.get("screenshot")))


def _close(session: str) -> str:
    try:
        import httpx
        httpx.post(f"{_bs._BROWSER_URL}/cleanup", json={"session": session}, timeout=10)
    except Exception as e:
        return f"已请求关闭 (cleanup 未响应: {e}; 空闲超时后会自动回收)"
    return f"已关闭浏览器会话 {session}"


async def _run(fn, *a) -> str:
    if _bs is None:
        return "ERROR: 浏览器模块不可用 (browser_search 未安装)"
    loop = asyncio.get_event_loop()
    if not await loop.run_in_executor(None, _bs._ensure_browser_server, 30.0):
        return f"ERROR: 浏览器服务起不来: {getattr(_bs, '_BROWSER_LAST_ERR', '')}"
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, fn, *a), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        return f"ERROR: 浏览器操作超过 {_TIMEOUT}s"


@register("browser_snapshot")
async def h_browser_snapshot(sid: str, args: dict) -> tuple[str, Any]:
    session = _session(sid)
    url = (args.get("url") or "").strip()
    shot = bool(args.get("screenshot"))

    def _go() -> str:
        if url:
            r = _post("goto", {"url": url}, session)
            if not r.get("ok"):
                return f"ERROR: {r.get('msg', '打开失败')}"
        return _snapshot(session, shot)
    return await _run(_go), None


@register("browser_act")
async def h_browser_act(sid: str, args: dict) -> tuple[str, Any]:
    session = _session(sid)
    if (args.get("action") or "").strip() == "close":
        if _bs is None:
            return "浏览器模块不可用, 无需关闭", None
        return await asyncio.get_event_loop().run_in_executor(None, _close, session), None
    return await _run(_act, session, args), None

