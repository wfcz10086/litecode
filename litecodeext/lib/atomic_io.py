"""lib/atomic_io.py — 原子落盘 helper (fsync-safe).

关键路径 (session / dag_job / checkpoint / artifact meta) 用此模块写盘,
保证进程崩溃 / kill -9 / 掉电后不会出现半写文件.

写策略 (POSIX): tmp write → fsync(tmp fd) → os.replace(tmp, target) → fsync(parent dir fd).
"""
from __future__ import annotations
import json
import os
import uuid
from pathlib import Path
from typing import Any, Union

_PathLike = Union[str, Path]


def _fsync_dir(path: Path) -> None:
    """把 dir metadata 落盘 (POSIX 上 replace 后必须 fsync 父目录才能真持久)."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, PermissionError):
        pass


def atomic_write_bytes(target: _PathLike, data: bytes) -> None:
    """原子写二进制. tmp→fsync→replace→fsync(parent)."""
    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 唯一 tmp 名 (pid+random) — 避免同 target 并发写共享同一 tmp 文件,
    # 否则先完成的 os.replace 会把另一写者的 tmp "偷走", 后者 replace 时 FileNotFoundError
    tmp = p.with_suffix(p.suffix + f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(p))
        _fsync_dir(p.parent)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def atomic_write_text(target: _PathLike, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(target, text.encode(encoding))


def atomic_write_json(target: _PathLike, obj: Any, *, indent: int = 2,
                      ensure_ascii: bool = False) -> None:
    """原子写 JSON (常用于 session/dag_job snapshot)."""
    atomic_write_text(target, json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent))
