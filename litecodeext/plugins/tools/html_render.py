"""html_render.py — HTML 预览/截图 plugin (#45).

一个 tool: `html_render`
  - 输入: html (raw HTML 字符串) 或 url (远程) — 二选一
  - 可选: viewport_width (默认 1280) / viewport_height (默认 800)
          full_page (默认 True) / wait_selector / wait_ms
  - 输出: PNG 截图存 sessions/{sid}/artifacts/, 同时把 .html 源写盘做 iframe 预览
  - 走 playwright chromium 单例 + 用完即关 (`feedback_browser_singleton`)

前端: PNG 走已有 artifact 面板自动预览; 不改 UI 层 (chassis-not-integration).
"""
from __future__ import annotations
import logging
import time
from pathlib import Path
from typing import Optional

from lib.config import SESSIONS_DISK

log = logging.getLogger("litecode_server")


_MAX_HTML_BYTES = 2 * 1024 * 1024  # 2MB 硬顶, 防止跑飞


def _artifact_dir(sid: str) -> Path:
    d = SESSIONS_DISK / (sid or "_orphan") / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _render(
    *,
    html: Optional[str],
    url: Optional[str],
    out_png: Path,
    viewport_width: int,
    viewport_height: int,
    full_page: bool,
    wait_selector: Optional[str],
    wait_ms: int,
) -> dict:
    """真正跑 playwright. 返回 {ok, w, h, bytes, error?}."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "error": "playwright 未安装 (pip install playwright && playwright install chromium)"}

    pw = browser = ctx = page = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            locale="zh-CN",
        )
        page = await ctx.new_page()
        if url:
            await page.goto(url, wait_until="load", timeout=20_000)
        else:
            # 空 about:blank 打底 → set_content, 再等 load; 避免 chromium
            # 在 empty frame 上直接 screenshot 报 "Unable to capture screenshot".
            await page.goto("about:blank")
            await page.set_content(html or "", wait_until="load", timeout=15_000)
        if wait_selector:
            try:
                await page.wait_for_selector(wait_selector, timeout=8_000)
            except Exception as _wse:
                log.warning(f"  [html_render] wait_selector 未命中: {_wse}")
        if wait_ms and wait_ms > 0:
            await page.wait_for_timeout(min(int(wait_ms), 8_000))
        # 强制刷一次 layout + 让 chromium paint (headless 首帧竞态防御).
        # 在某些 chromium 构建里, load 事件后立刻 screenshot 会报
        # "Unable to capture screenshot" (frame 还没 commit).
        try:
            await page.evaluate("() => void document.documentElement.getBoundingClientRect()")
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass
        # 双 requestAnimationFrame → 确保 chromium 已经 commit 首帧
        try:
            await page.evaluate(
                "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            )
        except Exception:
            pass
        raw = None
        # Some headless-chromium builds fail full_page with
        # "Unable to capture screenshot"; fall back to viewport-only.
        try:
            raw = await page.screenshot(path=str(out_png), full_page=bool(full_page))
        except Exception as _e1:
            if full_page:
                log.warning(f"  [html_render] full_page 截图失败, 降级到 viewport: {_e1}")
                raw = await page.screenshot(path=str(out_png), full_page=False)
            else:
                raise
        size_hint = out_png.stat().st_size if out_png.exists() else len(raw or b"")
        return {"ok": True, "w": viewport_width, "h": viewport_height, "bytes": size_hint}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        for closer in (page, ctx, browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:
                    pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass


async def tool_html_render(args: dict, sid: Optional[str], ctx: Optional[dict]):
    html_src = args.get("html")
    url = args.get("url")
    if not html_src and not url:
        return "ERROR: html_render 需要 html 或 url 至少一项", None
    if html_src and url:
        return "ERROR: html_render 的 html 与 url 二选一, 不能同传", None
    if html_src and len(html_src.encode("utf-8", errors="ignore")) > _MAX_HTML_BYTES:
        return f"ERROR: html 长度超过 {_MAX_HTML_BYTES // 1024}KB 硬顶", None

    vw = int(args.get("viewport_width", 1280))
    vh = int(args.get("viewport_height", 800))
    full = bool(args.get("full_page", True))
    wait_sel = args.get("wait_selector") or None
    wait_ms = int(args.get("wait_ms", 0))
    vw = max(120, min(vw, 3840))
    vh = max(120, min(vh, 4320))

    ts = int(time.time() * 1000)
    art_dir = _artifact_dir(sid or "_orphan")
    png_path = art_dir / f"html_render_{ts}.png"
    html_path = art_dir / f"html_render_{ts}.html" if html_src else None
    if html_src and html_path is not None:
        try:
            html_path.write_text(html_src, encoding="utf-8")
        except Exception as _we:
            log.warning(f"  [html_render] 写 html 源失败: {_we}")

    ret = await _render(
        html=html_src, url=url, out_png=png_path,
        viewport_width=vw, viewport_height=vh, full_page=full,
        wait_selector=wait_sel, wait_ms=wait_ms,
    )

    if not ret.get("ok"):
        return f"ERROR html_render: {ret.get('error', 'unknown')}", None

    # 挂 artifact (让 web UI artifact 面板自动显示 PNG)
    add_art = (ctx or {}).get("add_artifact_fn")
    if add_art and sid:
        try:
            add_art(sid, {
                "filepath": str(png_path),
                "op": "html_render",
                "size": ret.get("bytes", 0),
                "ts": time.time(),
                "kind": "png",
                "source_html": str(html_path) if html_path else None,
                "source_url": url or None,
            })
        except Exception as _ae:
            log.warning(f"  [html_render] add_artifact 失败: {_ae}")

    src_label = f"url={url}" if url else f"html ({len(html_src or '')} chars)"
    return (
        f"[html_render OK] {src_label}\n"
        f"  png: {png_path}\n"
        f"  size: {ret.get('bytes', 0)} bytes  viewport: {vw}x{vh}  full_page: {full}"
    ), None


# ── plugin 契约 ───────────────────────────────────────────────
_TOOLS = {"html_render": tool_html_render}


def names() -> set[str]:
    return set(_TOOLS.keys())


async def dispatch(name: str, args: dict, sid: Optional[str] = None, ctx: Optional[dict] = None):
    fn = _TOOLS.get(name)
    if not fn:
        return None
    return await fn(args, sid, ctx or {})


def _wrap(fn):
    async def _run(args, ctx):
        return await fn(args or {}, (ctx or {}).get("sid"), ctx or {})
    _run.__name__ = f"run_{fn.__name__}"
    return _run


TOOLS = [
    {
        "name": "html_render",
        "description": "把 HTML 或 URL 用 headless chromium 渲染成 PNG 截图 (存 artifact); 用完即关.",
        "schema": {
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "raw HTML 源码 (与 url 二选一)"},
                "url":  {"type": "string", "description": "远程 URL (与 html 二选一)"},
                "viewport_width":  {"type": "integer", "description": "viewport 宽 (120-3840, 默认 1280)"},
                "viewport_height": {"type": "integer", "description": "viewport 高 (120-4320, 默认 800)"},
                "full_page":     {"type": "boolean", "description": "整页截图 (默认 true)"},
                "wait_selector": {"type": "string",  "description": "等待选择器出现再截图 (最长 8s)"},
                "wait_ms":       {"type": "integer", "description": "额外静态等待毫秒 (0-8000)"},
            },
        },
        "run": _wrap(tool_html_render),
        "capabilities": {"cost": "medium", "safe": True},
    },
]
VERSION = "0.1.0"
