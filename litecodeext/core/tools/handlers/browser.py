"""handlers/browser.py — browser_login + browser_read (真实浏览器)."""
from __future__ import annotations

import asyncio
from typing import Any

from lib.config import WORKSPACE

try:
    import browser_search as _bs
    HAS_BROWSER_SEARCH = True
except ImportError:
    _bs = None
    HAS_BROWSER_SEARCH = False

from . import register


@register("browser_login")
async def h_browser_login(sid: str, args: dict) -> tuple[str, Any]:
    if not HAS_BROWSER_SEARCH or not _bs:
        return "ERROR: browser_login 不可用 (browser_search module 缺)", None
    url = args.get("url", "")
    if not url:
        return "ERROR: browser_login 需要 url 参数", None
    try:
        from lib.browser_login import attempt_login as _alogin
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _alogin, WORKSPACE, url,
                args.get("username", ""), args.get("password", ""),
                args.get("domain"), int(args.get("timeout", 30)),
                args.get("user_hint"), args.get("submit_selector"),
            ),
            timeout=60,
        )
        msg_parts = [
            ("✅" if r.get("ok") else "❌") + " " + (r.get("msg") or ""),
            f"URL: {r.get('url', '')}",
            f"填充字段: {r.get('fields_filled', 0)}",
            f"selectors: {r.get('selectors', {})}",
        ]
        if r.get("screenshot"):
            msg_parts.append(f"截图: {r['screenshot']}")
        return "\n".join(msg_parts), None
    except asyncio.TimeoutError:
        return "ERROR: browser_login 超过 60s 超时", None
    except Exception as e:
        return f"ERROR: browser_login 失败: {e}", None


@register("browser_search")
async def h_browser_search(sid: str, args: dict) -> tuple[str, Any]:
    if not HAS_BROWSER_SEARCH or not _bs:
        return "ERROR: browser_search module 不可用 (检查 skills/browser-automation 是否正常安装)", None
    query = args.get("query", "")
    if not query:
        return "ERROR: browser_search 需要 query 参数", None
    engine = args.get("engine", "bing")
    top_k = min(int(args.get("top_k", 5)), 15)
    shot = bool(args.get("screenshot", True))
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(None, _bs.browser_search, query, engine, top_k, shot),
            timeout=45,
        )
        return r.get("message") or "ERROR: browser_search 无返回", None
    except asyncio.TimeoutError:
        return "ERROR: browser_search 超过 45s 超时", None
    except Exception as e:
        return f"ERROR: browser_search 失败: {e}", None


@register("browser_read")
async def h_browser_read(sid: str, args: dict) -> tuple[str, Any]:
    if not HAS_BROWSER_SEARCH or not _bs:
        return "ERROR: browser_read module 不可用", None
    url = args.get("url", "")
    if not url:
        return "ERROR: browser_read 需要 url 参数", None
    wait_for  = args.get("wait_for")
    max_chars = int(args.get("max_chars", 8000))
    shot      = bool(args.get("screenshot", True))
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _bs.browser_read, url, wait_for, max_chars, shot
            ),
            timeout=45,
        )
        return r.get("message") or "ERROR: browser_read 无返回", None
    except asyncio.TimeoutError:
        return "ERROR: browser_read 超过 45s 超时", None
    except Exception as e:
        return f"ERROR: browser_read 失败: {e}", None
