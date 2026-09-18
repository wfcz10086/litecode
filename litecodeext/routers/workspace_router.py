"""workspace_router.py — workspace 文件浏览 / 下载 / 媒体预览 / office 转 PDF."""
import hashlib as _hashlib
import shutil as _shutil
import subprocess as _subprocess
from pathlib import Path
from urllib.parse import quote as _urlquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse


def _cd(mode: str, filename: str) -> str:
    """生成 RFC 6266 Content-Disposition，正确处理中文/非 ASCII 文件名."""
    try:
        filename.encode("ascii")
        return f'{mode}; filename="{filename}"'
    except UnicodeEncodeError:
        safe = filename.encode("ascii", errors="replace").decode()
        return f'{mode}; filename="{safe}"; filename*=UTF-8\'\'{_urlquote(filename)}'

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


_PREVIEW_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
_PREVIEW_TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
                      ".toml", ".ini", ".cfg", ".sh", ".bash", ".css", ".html", ".xml",
                      ".csv", ".tsv", ".log", ".conf", ".env", ".sql", ".go", ".rs", ".c",
                      ".cpp", ".h", ".java", ".rb", ".php", ".lua", ".r", ".m", ".swift"}
_PREVIEW_PDF_EXTS = {".pdf"}

_WX_SAFE_DIRS = [
    Path.home() / ".litecode" / "wechat_media",
    Path("/tmp/litecode_workspace/uploads"),
    Path("/tmp/litecode_workspace"),
    Path("/tmp/litecode_workspace"),
]
_MEDIA_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
}

_OFFICE_PDF_CACHE = Path("/tmp/litecode_workspace/.office_pdf_cache")
_OFFICE_PDF_CACHE.mkdir(parents=True, exist_ok=True)
_OFFICE_EXTS = {".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".odt", ".odp", ".ods"}


def _ws() -> Path:
    return S.workspace


@router.get("/api/workspace/files")
async def api_workspace_files(request: Request, path: str = ""):
    require_auth(request)
    base = _ws() / path if path else _ws()
    if not base.exists():
        return {"files": [], "path": str(base)}
    items = []
    try:
        for f in sorted(base.iterdir()):
            if f.name.startswith(".") or f.name == "__pycache__":
                continue
            items.append({
                "name": f.name,
                "path": str(f.relative_to(_ws())),
                "is_dir": f.is_dir(),
                "size": f.stat().st_size if f.is_file() else 0,
                "mtime": f.stat().st_mtime,
            })
    except Exception:
        pass
    return {"files": items, "path": path or "/"}


@router.get("/api/workspace/download")
async def api_workspace_download(request: Request, path: str = ""):
    require_auth(request)
    fp = _ws() / path
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "file not found")
    try:
        fp.resolve().relative_to(_ws().resolve())
    except ValueError:
        raise HTTPException(403, "forbidden")
    return FileResponse(fp, headers={"Content-Disposition": _cd("attachment", fp.name)})


@router.get("/api/media")
async def api_media(request: Request, path: str = ""):
    """预览/下载微信附件 (图片内联, PDF 新标签, 视频/音频 inline, 其他下载)."""
    require_auth(request)
    if not path:
        raise HTTPException(400, "path required")
    fp = Path(path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, f"file not found: {path}")
    resolved = fp.resolve()
    allowed = any(
        str(resolved).startswith(str(d.resolve()))
        for d in _WX_SAFE_DIRS if d.exists()
    )
    if not allowed:
        raise HTTPException(403, "forbidden: path outside allowed dirs")
    ext = fp.suffix.lower()
    mt = _MEDIA_MIME.get(ext, "application/octet-stream")
    inline_types = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
                    ".bmp", ".pdf", ".mp4", ".mov", ".webm", ".mp3", ".wav"}
    disposition = "inline" if ext in inline_types else _cd("attachment", fp.name)
    return FileResponse(
        fp, media_type=mt,
        headers={"Content-Disposition": disposition,
                 "Cache-Control": "private, max-age=3600"},
    )


@router.get("/api/preview")
async def api_preview_abs(request: Request, path: str = ""):
    """[P54+] 绝对路径预览 — chat 里 AI 产出的文件做内联预览."""
    require_auth(request)
    if not path:
        raise HTTPException(400, "path required")
    fp = Path(path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, f"file not found: {path}")
    resolved = fp.resolve()
    allowed = any(
        str(resolved).startswith(str(d.resolve()))
        for d in _WX_SAFE_DIRS if d.exists()
    )
    if not allowed:
        raise HTTPException(403, "forbidden: path outside whitelist")
    ext = fp.suffix.lower()
    sz = fp.stat().st_size
    if sz > 10 * 1024 * 1024:
        return {"type": "too_large", "name": fp.name, "size": sz, "ext": ext}
    if ext in _PREVIEW_TEXT_EXTS:
        try:
            content = fp.read_text(errors="replace")
            if len(content) > 500_000:
                content = content[:500_000] + "\n\n... [truncated] ..."
            return {"type": "text", "ext": ext, "content": content, "name": fp.name, "size": sz}
        except Exception as ex:
            return {"type": "error", "message": str(ex), "name": fp.name}
    if ext in _PREVIEW_IMG_EXTS:
        return {"type": "image", "name": fp.name, "ext": ext, "size": sz}
    if ext in _PREVIEW_PDF_EXTS:
        return {"type": "pdf", "name": fp.name, "ext": ext, "size": sz}
    if ext in {".mp4", ".mov", ".webm"}:
        return {"type": "video", "name": fp.name, "ext": ext, "size": sz}
    if ext in {".mp3", ".wav", ".ogg"}:
        return {"type": "audio", "name": fp.name, "ext": ext, "size": sz}
    return {"type": "unsupported", "name": fp.name, "ext": ext, "size": sz}


@router.get("/api/workspace/preview")
async def api_workspace_preview(request: Request, path: str = ""):
    """workspace 内文件在线预览 (相对路径)."""
    require_auth(request)
    fp = _ws() / path
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "file not found")
    try:
        fp.resolve().relative_to(_ws().resolve())
    except ValueError:
        raise HTTPException(403, "forbidden")
    ext = fp.suffix.lower()
    if ext in _PREVIEW_IMG_EXTS:
        media_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
            ".bmp": "image/bmp", ".ico": "image/x-icon",
        }
        return FileResponse(fp, media_type=media_map.get(ext, "application/octet-stream"),
                            headers={"Content-Disposition": _cd("inline", fp.name)})
    if ext in _PREVIEW_PDF_EXTS:
        return FileResponse(fp, media_type="application/pdf",
                            headers={"Content-Disposition": _cd("inline", fp.name)})
    if ext in {".mp4", ".webm", ".mov", ".m4v"}:
        vmap = {".mp4": "video/mp4", ".webm": "video/webm",
                ".mov": "video/quicktime", ".m4v": "video/x-m4v"}
        return FileResponse(fp, media_type=vmap.get(ext, "video/mp4"),
                            headers={"Content-Disposition": _cd("inline", fp.name)})
    if ext in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}:
        amap = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
                ".m4a": "audio/mp4", ".flac": "audio/flac"}
        return FileResponse(fp, media_type=amap.get(ext, "audio/mpeg"),
                            headers={"Content-Disposition": _cd("inline", fp.name)})
    if ext in _PREVIEW_TEXT_EXTS:
        try:
            content = fp.read_text(errors="replace")
            if len(content) > 500_000:
                content = content[:500_000] + "\n\n... [truncated, file too large] ..."
            return {"type": "text", "content": content, "name": fp.name, "size": fp.stat().st_size}
        except Exception as ex:
            return {"type": "error", "message": str(ex), "name": fp.name}
    return {"type": "unsupported", "name": fp.name, "ext": ext}


@router.api_route("/api/preview_office", methods=["GET", "HEAD"])
async def api_preview_office_abs(request: Request, path: str = ""):
    """[chat-pv-card] 绝对路径 Office 文档转 PDF，供 chat pv-card 内联预览用."""
    require_auth(request)
    if not _shutil.which("soffice") and not _shutil.which("libreoffice"):
        raise HTTPException(503, "libreoffice not installed — 重 build 镜像装 LibreOffice 后可用")
    if not path:
        raise HTTPException(400, "path required")
    fp = Path(path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, f"file not found: {path}")
    resolved = fp.resolve()
    allowed = any(
        str(resolved).startswith(str(d.resolve()))
        for d in _WX_SAFE_DIRS if d.exists()
    )
    if not allowed:
        raise HTTPException(403, "forbidden: path outside allowed dirs")
    ext = fp.suffix.lower()
    if ext not in _OFFICE_EXTS:
        raise HTTPException(400, f"not an office file: {ext}")
    _fkey = _hashlib.sha1(str(resolved).encode()).hexdigest()[:8]
    cache_pdf = _OFFICE_PDF_CACHE / f"{_fkey}_{fp.stem}.pdf"
    if not cache_pdf.exists() or cache_pdf.stat().st_mtime < fp.stat().st_mtime:
        soffice = _shutil.which("soffice") or _shutil.which("libreoffice") or "soffice"
        proc = _subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", str(_OFFICE_PDF_CACHE), str(fp)],
            capture_output=True, timeout=60,
        )
        produced = _OFFICE_PDF_CACHE / f"{fp.stem}.pdf"
        if proc.returncode != 0 or not produced.exists():
            raise HTTPException(500, f"office→pdf 转换失败: {proc.stderr.decode(errors='replace')[:200]}")
        if produced != cache_pdf:
            produced.rename(cache_pdf)
    return FileResponse(cache_pdf, media_type="application/pdf",
                        headers={"Content-Disposition": _cd("inline", fp.stem + ".pdf")})


@router.api_route("/api/workspace/preview_office", methods=["GET", "HEAD"])
async def api_workspace_preview_office(request: Request, path: str = ""):
    """[preview-2026-05] Office 文档转 PDF, 用 libreoffice headless + mtime cache."""
    require_auth(request)
    if not _shutil.which("soffice") and not _shutil.which("libreoffice"):
        raise HTTPException(503, "libreoffice not installed — 重 build 镜像装 LibreOffice 后可用")
    fp = _ws() / path
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "file not found")
    try:
        fp.resolve().relative_to(_ws().resolve())
    except ValueError:
        raise HTTPException(403, "forbidden")
    ext = fp.suffix.lower()
    if ext not in _OFFICE_EXTS:
        raise HTTPException(400, f"not an office file: {ext}")
    # 用 sha1 前8位做稳定 hash (Python hash() 受 PYTHONHASHSEED 随机化, 每次重启不同 → cache 永远命不中)
    _fkey = _hashlib.sha1(str(fp.resolve()).encode()).hexdigest()[:8]
    cache_pdf = _OFFICE_PDF_CACHE / f"{_fkey}_{fp.stem}.pdf"
    if not cache_pdf.exists() or cache_pdf.stat().st_mtime < fp.stat().st_mtime:
        soffice = _shutil.which("soffice") or _shutil.which("libreoffice") or "soffice"
        proc = _subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", str(_OFFICE_PDF_CACHE), str(fp)],
            capture_output=True, timeout=60,
        )
        produced = _OFFICE_PDF_CACHE / f"{fp.stem}.pdf"
        if proc.returncode != 0 or not produced.exists():
            raise HTTPException(500, f"office→pdf 转换失败: {proc.stderr.decode(errors='replace')[:200]}")
        if produced != cache_pdf:
            produced.rename(cache_pdf)
    return FileResponse(cache_pdf, media_type="application/pdf",
                        headers={"Content-Disposition": _cd("inline", fp.stem + ".pdf")})
