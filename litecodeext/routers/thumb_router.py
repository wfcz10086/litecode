"""thumb_router.py — 聊天图片缩略图 (GET /api/thumb?path=<uploads 内路径>&w=480).

[perf 2026-10] 长会话里工具截图都是 1280x800 原图 PNG (单张 100KB+), 聊天气泡只需要小预览。
- 只服务工作区 uploads 目录内的栅格图 (resolve 后校验, 防 ../ 与软链穿越); 其他一律 403
- PIL 生成 WebP (不支持时退 JPEG), 落盘缓存: 键 = 解析后路径 + mtime_ns + size + 宽度
- 鉴权与 /api/media 一致 (require_auth); 带 ETag + Cache-Control, 二次加载 304
点击缩略图看原图仍走 /api/media (前端 data-full)。
"""
import hashlib
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()
ORDER = 61

THUMB_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}   # gif 动图/svg 矢量不缩
WIDTHS = (160, 240, 320, 480, 640, 960, 1280)               # 宽度分档, 防缓存爆炸
MAX_SRC_BYTES = 40 * 1024 * 1024
CACHE_CONTROL = "private, max-age=86400"


def uploads_dir() -> Path:
    return (Path(S.workspace) / "uploads").resolve()


def cache_dir() -> Path:
    return Path(S.workspace) / ".thumb_cache"


CACHE_CAP_BYTES = 500 * 1024 * 1024   # 缩略图缓存总量上限, 超出按最旧 (mtime) 淘汰


def evict_cache(cdir: Path, cap: int = CACHE_CAP_BYTES) -> None:
    """缓存目录超过 cap 时删最旧的文件直到降到 cap 的 90% 以下。只在写入新缩略图 (miss) 后调用。"""
    try:
        files = []
        total = 0
        for f in cdir.iterdir():
            if not f.is_file():
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, f))
            total += st.st_size
        if total <= cap:
            return
        files.sort()                                  # 最旧的在前
        target = int(cap * 0.9)
        for _mtime, size, f in files:
            if total <= target:
                break
            try:
                f.unlink()
                total -= size
            except OSError:
                pass
    except Exception:
        pass


def bucket_width(w: int) -> int:
    for b in WIDTHS:
        if w <= b:
            return b
    return WIDTHS[-1]


def resolve_upload(path: str) -> Path:
    """path 可为绝对路径或相对 uploads 的路径; 解析 (含软链) 后必须仍在 uploads 内。"""
    if not path or "\x00" in path:
        raise HTTPException(400, "path required")
    up = uploads_dir()
    p = Path(path)
    fp = (p if p.is_absolute() else up / p).resolve()
    if fp == up or not fp.is_relative_to(up):
        raise HTTPException(403, "forbidden: only workspace uploads images")
    if fp.suffix.lower() not in THUMB_EXTS:
        raise HTTPException(415, f"unsupported image type: {fp.suffix}")
    if not fp.is_file():
        raise HTTPException(404, "file not found")
    return fp


def _webp_ok() -> bool:
    try:
        from PIL import features
        return bool(features.check("webp"))
    except Exception:
        return False


def make_thumb(src: Path, w: int, dst: Path, fmt: str) -> None:
    from PIL import Image
    with Image.open(src) as im:
        im.draft("RGB", (w, w))          # JPEG 解码期降采样, 其他格式无副作用
        im.thumbnail((w, w * 4))          # 只限宽 (高给足), 保持比例, 不放大
        if fmt == "JPEG":
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
        elif im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")   # 并发生成同一张互不踩
        try:
            im.save(tmp, fmt, quality=78, **({"method": 4} if fmt == "WEBP" else {"optimize": True}))
            tmp.replace(dst)
        finally:
            tmp.unlink(missing_ok=True)


@router.get("/api/thumb")
async def api_thumb(request: Request, path: str = "", w: int = 480):
    require_auth(request)
    fp = resolve_upload(path)
    st = fp.stat()
    if st.st_size > MAX_SRC_BYTES:
        raise HTTPException(413, "image too large for thumbnail")
    width = bucket_width(max(1, w))
    fmt, ext, mime = ("WEBP", "webp", "image/webp") if _webp_ok() else ("JPEG", "jpg", "image/jpeg")
    key = hashlib.sha1(f"{fp}|{st.st_mtime_ns}|{st.st_size}|{width}|{fmt}".encode()).hexdigest()
    etag = f'"{key[:20]}"'
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    cdir = cache_dir()
    dst = cdir / f"{key}.{ext}"
    hit = dst.is_file()
    if not hit:
        cdir.mkdir(parents=True, exist_ok=True)
        try:
            await run_in_threadpool(make_thumb, fp, width, dst, fmt)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(422, f"thumbnail failed: {str(e)[:120]}")
        await run_in_threadpool(evict_cache, cdir)   # 只在新写入后检查容量, 命中不触发
    headers["X-Thumb-Cache"] = "hit" if hit else "miss"
    return FileResponse(dst, media_type=mime, headers=headers)
