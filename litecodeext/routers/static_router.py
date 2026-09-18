"""static_router.py — 静态资源 (/assets/*) + 主页 HTML (/).

主页 HTML 走 _get_html() 函数 (注入 VNC/WebPort 后返回), 由 web_ui.py 注入.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from routers.state import S

router = APIRouter()


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


@router.get("/artifacts", response_class=HTMLResponse)
async def artifacts_page():
    """Artifact 管理页 (S1-N): timer / bg / plugin 通用产出物 dashboard."""
    fp = S.base_dir / "web_assets" / "artifacts.html"
    if not fp.exists():
        raise HTTPException(404, "artifacts.html not found")
    return FileResponse(str(fp), media_type="text/html; charset=utf-8")


@router.get("/", response_class=HTMLResponse)
async def index():
    """主页 HTML — 由 web_ui.py 注入 _get_html 函数."""
    if S.get_html is None:
        return HTMLResponse("<h1>get_html not injected</h1>", status_code=500)
    return S.get_html()
