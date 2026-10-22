"""upload_router.py — Web 文件上传 /api/upload/{sid}: multipart 边收边写盘, 不进内存、不二次落盘。

[large-file 2026-10-21] 原实现用 FastAPI `UploadFile = File(...)` 声明参数, Starlette 会先把整个
请求体解析进 SpooledTemporaryFile (>1MB 落 /tmp), 处理函数拿到时上传早已收完:
  - 大小上限只能在整份收完后才判 (1GB 超限文件也要先吃完 1GB 才 413);
  - /tmp 临时文件 + uploads/ 目标文件 = 双倍磁盘写;
  - 413 只给 {"detail"}, 前端读 d.error 只显示 "HTTP 413"。
现在:
  - Content-Length 已知且超限 → 不读请求体直接 413;
  - 无 Content-Length (chunked) 时边收边计数, 超限立刻中止并删半成品;
  - python-multipart 底层解析器回调直接写 uploads/.upload-<id>.part, 收完 os.replace 成正式名;
  - 边写边算 sha256 回给调用方, 客户端可据此核对完整性;
  - 客户端中途断开 / 解析失败 → 半成品一定删掉;
  - 错误体同时带 error 与 detail, 前端 (d.error) 和 CLI (detail) 都能显示原因。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()
ORDER = 55

DEFAULT_MAX_UPLOAD_MB = 1024          # config.web_ui.max_upload_mb 缺省值
MULTIPART_SLACK = 64 * 1024           # multipart 边界 / 头部 / 小字段的开销余量
DISK_RESERVE = 256 * 1024 * 1024      # 落盘后至少给磁盘留这么多
_FIELD_CAP = 64 * 1024                # 非文件字段 (很少用) 最多收这么多
_FLUSH = 1024 * 1024                  # 请求体攒够 1MB 再丢线程池解析+写盘, 不阻塞事件循环


def max_upload_mb(cfg: dict | None = None) -> int:
    cfg = S.cfg if cfg is None else cfg
    try:
        mb = int(((cfg or {}).get("web_ui") or {}).get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB))
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_UPLOAD_MB
    return max(1, mb)


def safe_filename(name: str | None) -> str:
    """只留末段文件名: 去目录 (/ 与 \\ 都算)、去 NUL、挡 . / ..; 超长时保留后缀截短。"""
    base = (name or "").replace("\\", "/").split("/")[-1].replace("\x00", "").strip()
    if base in ("", ".", "..") or set(base) <= {"."}:
        base = "unnamed"
    if len(base.encode()) > 200:
        stem, suf = os.path.splitext(base)
        suf = suf if len(suf.encode()) <= 20 else ""
        stem = stem.encode()[: 200 - len(suf.encode())].decode(errors="ignore")
        base = (stem or "unnamed") + suf
    return base


def _unique_target(dest: Path, name: str) -> Path:
    fp = dest / name
    if not fp.exists():
        return fp
    stem, suf = os.path.splitext(name)
    ts = int(time.time())
    fp = dest / f"{stem}-{ts}{suf}"
    n = 1
    while fp.exists():
        fp = dest / f"{stem}-{ts}-{n}{suf}"
        n += 1
    return fp


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg, "detail": msg}, status_code=status)


class _TooLarge(Exception):
    pass


class _Sink:
    """python-multipart 回调: 第一个带 filename 的 part 写进 .part 文件, 其余字段只收一小段。"""

    def __init__(self, dest: Path, limit: int):
        self.dest, self.limit = dest, limit
        self.part_path: Path | None = None
        self.fh = None
        self.filename: str | None = None
        self.size = 0
        self.sha = hashlib.sha256()
        self.done_file = False
        self._hname = b""
        self._hval = b""
        self._headers: dict[bytes, bytes] = {}
        self._cur_is_file = False
        self._field_len = 0

    # ---- 回调 (同步, 在线程池里跑) ----
    def on_part_begin(self):
        self._headers, self._cur_is_file, self._field_len = {}, False, 0

    def on_header_field(self, data, start, end):
        self._hname += data[start:end]

    def on_header_value(self, data, start, end):
        self._hval += data[start:end]

    def on_header_end(self):
        self._headers[self._hname.lower()] = self._hval
        self._hname, self._hval = b"", b""

    def on_headers_finished(self):
        from python_multipart.multipart import parse_options_header
        _, opts = parse_options_header(self._headers.get(b"content-disposition", b""))
        fname = opts.get(b"filename")
        if fname is not None and not self.done_file and self.fh is None:
            self.filename = fname.decode("utf-8", errors="replace")
            self.part_path = self.dest / f".upload-{uuid.uuid4().hex}.part"
            self.fh = open(self.part_path, "wb")
            self._cur_is_file = True

    def on_part_data(self, data, start, end):
        n = end - start
        if not self._cur_is_file:          # 非文件字段不存内容, 只防有人塞超大字段
            self._field_len += n
            if self._field_len > _FIELD_CAP:
                raise _TooLarge()
            return
        self.size += n
        if self.size > self.limit:
            raise _TooLarge()
        chunk = data[start:end]
        self.sha.update(chunk)
        self.fh.write(chunk)

    def on_part_end(self):
        if self._cur_is_file:
            self.fh.close()
            self.fh = None
            self.done_file = True
            self._cur_is_file = False

    def cleanup(self):
        try:
            if self.fh is not None:
                self.fh.close()
        except Exception:
            pass
        self.fh = None
        if self.part_path is not None:
            try:
                self.part_path.unlink()
            except FileNotFoundError:
                pass


async def receive_upload(request: Request, dest: Path, limit: int) -> dict | JSONResponse:
    """把 multipart 请求里的 file 字段流式写进 dest, 返回 {path,name,size,sha256} 或错误响应。"""
    from python_multipart.multipart import MultipartParser, parse_options_header
    mb = limit // 1048576
    too_big = (f"文件过大，上传上限 {mb}MB — 大文件请先压缩/分卷, 或改 config.web_ui.max_upload_mb, "
               f"或用 scp/rsync 直接放进工作区 uploads/ 目录")
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        return _err(400, "需要 multipart/form-data 上传 (字段名 file)")
    cl = request.headers.get("content-length", "")
    if cl.isdigit():
        if int(cl) > limit + MULTIPART_SLACK:
            return _err(413, too_big + f" (本次 {int(cl) / 1048576:.1f}MB)")
        free = shutil.disk_usage(dest).free
        if int(cl) + DISK_RESERVE > free:
            return _err(507, f"服务端磁盘空间不足 (剩余 {free // 1048576}MB), 上传已拒绝")
    sink = _Sink(dest, limit)
    cbs = {k: getattr(sink, k) for k in ("on_part_begin", "on_header_field", "on_header_value",
                                         "on_header_end", "on_headers_finished", "on_part_data",
                                         "on_part_end")}
    parser = MultipartParser(boundary, cbs)
    try:
        # uvicorn 每次给 ~64KB, 攒到 1MB 再进线程池解析+写盘: 少 15 倍线程切换, 事件循环不被磁盘 IO 卡住
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) >= _FLUSH:
                data, buf = bytes(buf), bytearray()
                await asyncio.to_thread(parser.write, data)
        if buf:
            await asyncio.to_thread(parser.write, bytes(buf))
        await asyncio.to_thread(parser.finalize)
    except _TooLarge:
        sink.cleanup()
        return _err(413, too_big)
    except ClientDisconnect:
        sink.cleanup()
        return _err(400, "客户端中途断开, 半成品已删除")
    except Exception as e:  # 畸形 multipart / 磁盘写失败
        sink.cleanup()
        return _err(400, f"上传解析失败: {type(e).__name__}: {e}")
    if not sink.done_file or sink.part_path is None:
        sink.cleanup()
        return _err(400, "请求里没有完整的文件字段 (multipart 字段名 file)")
    fp = _unique_target(dest, safe_filename(sink.filename))
    os.replace(sink.part_path, fp)
    return {"path": str(fp), "name": fp.name, "size": sink.size, "sha256": sink.sha.hexdigest()}


@router.get("/api/upload_limits")
async def api_upload_limits(request: Request):
    require_auth(request)
    mb = max_upload_mb()
    return {"max_upload_mb": mb, "max_upload_bytes": mb * 1048576}


@router.post("/api/upload/{sid}")
async def api_upload(sid: str, request: Request):
    require_auth(request)
    # [P0-#4] upload 节流: 10 次 / 60s / IP (大附件重, 数值给紧)
    from lib.rate_limit import rate_check
    try:
        rate_check(request, "upload", 10, 60)
    except HTTPException as e:
        return _err(e.status_code, str(e.detail))
    dest = Path(S.workspace) / "uploads"
    dest.mkdir(parents=True, exist_ok=True)
    res = await receive_upload(request, dest, max_upload_mb() * 1048576)
    if isinstance(res, JSONResponse):
        return res
    # 兼容老协议 {ok,path,name,size}; 新增 sha256
    return {"ok": True, **res}
