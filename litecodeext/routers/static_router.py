"""static_router.py — 静态资源 (/assets/*) + 主页 HTML (/).

主页 HTML 走 _get_html() 函数 (注入 VNC/WebPort 后返回), 由 web_ui.py 注入;
仓外插件的前端入口由 plugin_assets_router.inject_html() 在返回前追加.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from routers.plugin_assets_router import inject_html
from routers.state import S

router = APIRouter()
ORDER = 999  # 注册顺序: 全部路由之后 (兜底静态资源)


_ASSET_MIME = {
    ".css":   "text/css; charset=utf-8",
    ".js":    "application/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff":  "font/woff",
    ".png":   "image/png",
    ".svg":   "image/svg+xml",
}


@router.get("/assets/{name:path}")
async def serve_asset(name: str):
    """提供本地 web_assets/ 下的静态资源 (css/js/font)."""
    assets_dir = S.base_dir / "web_assets"
    if ".." in name or name.startswith("/"):
        raise HTTPException(404)
    fp = (assets_dir / name).resolve()
    try:
        fp.relative_to(assets_dir.resolve())
    except ValueError:
        raise HTTPException(404)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, f"asset not found: {name}")
    media = _ASSET_MIME.get(fp.suffix.lower(), "application/octet-stream")
    return FileResponse(fp, media_type=media)


# [sec 2026-10-19] 主页 / artifacts 页的 CSP。
# 前端大量使用内联事件 (web_ui.html ~37 处 + JS 拼接的 ~119 处 onclick=) 和少量内联 <script>,
# 一次性改成 nonce/addEventListener 牵动面太大, 所以 script-src 仍含 'unsafe-inline';
# XSS 主防线是 DOMPurify (web_assets/safe_html.js, 所有 marked 渲染点)。这里补的是:
#   - 只允许同源脚本 (drawflow 已于 2026-10-21 本地化到 web_assets/vendor/, 不再放行 cdn.jsdelivr.net),
#     注入的 <script src=evil> 加载不了
#   - object-src 'none' / base-uri 'self' (防 <base> 劫持相对路径) / form-action 'self'
#   - frame-ancestors 'self' (防点击劫持)
PAGE_CSP = ("script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
PAGE_HEADERS = {
    "Content-Security-Policy": PAGE_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}


@router.get("/artifacts", response_class=HTMLResponse)
async def artifacts_page():
    """Artifact 管理页 (S1-N): timer / bg / plugin 通用产出物 dashboard."""
    fp = S.base_dir / "web_assets" / "artifacts.html"
    if not fp.exists():
        raise HTTPException(404, "artifacts.html not found")
    return FileResponse(str(fp), media_type="text/html; charset=utf-8", headers=PAGE_HEADERS)


@router.get("/", response_class=HTMLResponse)
async def index():
    """主页 HTML — 由 web_ui.py 注入 _get_html 函数; 再注入仓外插件的 WEB_ENTRY (无插件时原样)."""
    if S.get_html is None:
        return HTMLResponse("<h1>get_html not injected</h1>", status_code=500)
    return HTMLResponse(inject_html(S.get_html()), headers=PAGE_HEADERS)
