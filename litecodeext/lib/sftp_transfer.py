"""sftp_transfer.py — SFTP 写远程文件的完整性 + 原子替换 + 大文件断点续传 (ssh_write_file / ssh_sync_files 共用)。

[large-file 2026-10-21] 原实现: rename 原文件为 .bak → open(path,'w') 直接写目标 → 不校验。问题:
  - 写到一半断线, 目标文件是半截的 (原文件在 .bak, 服务已经读到坏文件);
  - SFTPv3 rename 目标已存在会失败 (OpenSSH), 旧 .bak 在时备份静默失败, 随后原文件被覆盖;
  - 没有任何校验; 只能传模型生成的 content, 本地大文件/二进制传不了。
现在 (对齐 rsync --partial / scp + sha256sum 的一线做法):
  1. 先写同目录临时文件 (内容写入: <path>.lctmp-<id>; 本地文件上传: <path>.lcpart, 可续传);
  2. 远端算 sha256 (sha256sum / shasum, 不行就 SFTP 回读流式算) 与本地比对, 不一致删临时文件报错;
  3. 校验过才备份原文件 (posix_rename 覆盖旧 .bak) 并把临时文件 rename 成目标 —— 目标要么旧要么新, 不会半截;
  4. 恢复原文件权限 / 属主。
本模块不 import paramiko, sftp / client 由调用方传入, 便于用本地文件系统假对象测试。
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat as _stat
import uuid
from typing import Callable

CHUNK = 1024 * 1024
MAX_LOCAL_UPLOAD = 20 * 1024 * 1024 * 1024    # 20GB, 防误传整块盘镜像
_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")

RemoteExec = Callable[[str], "tuple[int, str, str] | None"]


class IntegrityError(Exception):
    pass


def sha256_file(path: str, upto: int | None = None) -> str:
    return _hasher_upto(path, upto).hexdigest()


def _hasher_upto(path: str, upto: int | None = None):
    h = hashlib.sha256()
    left = upto
    with open(path, "rb") as f:
        while left is None or left > 0:
            b = f.read(CHUNK if left is None else min(CHUNK, left))
            if not b:
                break
            h.update(b)
            if left is not None:
                left -= len(b)
    return h


def _sftp_readback_sha(sftp, path: str) -> str:
    h = hashlib.sha256()
    with sftp.open(path, "rb") as f:
        if hasattr(f, "prefetch"):
            try:
                f.prefetch()
            except Exception:
                pass
        while True:
            b = f.read(CHUNK)
            h.update(b)
            if len(b) < CHUNK:       # paramiko read(n) 读满 n 或到 EOF, 短读即结束
                break
    return h.hexdigest()


def remote_sha256(sftp, path: str, run: RemoteExec | None = None) -> tuple[str, str]:
    """远端文件 sha256 → (hex, 方法)。优先远端命令 (不回传数据), 失败退回 SFTP 回读。"""
    if run is not None:
        q = shlex.quote(path)
        for cmd in (f"sha256sum -- {q}", f"shasum -a 256 -- {q}"):
            try:
                res = run(cmd)
            except Exception:
                res = None
            if res and res[0] == 0:
                m = _HEX64.search(res[1] or "")
                if m:
                    return m.group(1).lower(), cmd.split()[0]
    return _sftp_readback_sha(sftp, path), "sftp-readback"


def _exists(sftp, path: str):
    try:
        return sftp.stat(path)
    except Exception:
        return None


def rename_over(sftp, src: str, dst: str) -> None:
    """rename 且覆盖已存在目标: 优先 posix-rename@openssh.com, 否则先删再改名。"""
    pr = getattr(sftp, "posix_rename", None)
    if pr is not None:
        try:
            pr(src, dst)
            return
        except Exception:
            pass
    if _exists(sftp, dst) is not None:
        try:
            sftp.remove(dst)
        except Exception:
            pass
    sftp.rename(src, dst)


def _restore_meta(sftp, path: str, st) -> None:
    if st is None or st.st_mode is None:
        return
    try:
        sftp.chmod(path, _stat.S_IMODE(st.st_mode))
    except Exception:
        pass
    if getattr(st, "st_uid", None) is not None and getattr(st, "st_gid", None) is not None:
        try:
            sftp.chown(path, st.st_uid, st.st_gid)
        except Exception:
            pass


def _commit(sftp, tmp: str, path: str, backup: bool, st) -> None:
    if backup and st is not None:
        rename_over(sftp, path, path + ".bak")
    rename_over(sftp, tmp, path)
    _restore_meta(sftp, path, st)


def _safe_remove(sftp, path: str) -> None:
    try:
        sftp.remove(path)
    except Exception:
        pass


def write_bytes(sftp, path: str, data: bytes, backup: bool = True, run: RemoteExec | None = None) -> dict:
    """内容写入: 临时文件 → sha256 校验 → (备份) → 原子改名 → 恢复权限。"""
    st = _exists(sftp, path)
    tmp = f"{path}.lctmp-{uuid.uuid4().hex[:8]}"
    want = hashlib.sha256(data).hexdigest()
    try:
        with sftp.open(tmp, "wb") as f:
            f.write(data)
        got, how = remote_sha256(sftp, tmp, run)
        if got != want:
            raise IntegrityError(f"sha256 不一致 (本地 {want[:12]}… / 远端 {got[:12]}…), 目标文件未改动")
        _commit(sftp, tmp, path, backup, st)
    except Exception:
        _safe_remove(sftp, tmp)
        raise
    return {"path": path, "size": len(data), "sha256": want, "verify": how}


def upload_file(sftp, local: str, path: str, backup: bool = True, resume: bool = True,
                run: RemoteExec | None = None) -> dict:
    """本地文件流式上传: 写 <path>.lcpart (resume 时从已有长度续传, 先核对已传部分的 sha256),
    全部写完核对整体 sha256 再原子替换目标。内存占用 = 1 个块。"""
    size = os.path.getsize(local)
    if size > MAX_LOCAL_UPLOAD:
        raise ValueError(f"文件 {size / 1073741824:.1f}GB 超过 {MAX_LOCAL_UPLOAD // 1073741824}GB 上限")
    st = _exists(sftp, path)
    part = path + ".lcpart"
    offset, resumed = 0, False
    full = hashlib.sha256()
    pst = _exists(sftp, part) if resume else None
    if pst is not None and 0 < (pst.st_size or 0) <= size:
        have, _ = remote_sha256(sftp, part, run)
        pre = _hasher_upto(local, pst.st_size)       # 本地前缀哈希; 一致则直接接着当整体哈希用
        if have == pre.hexdigest():
            offset, resumed, full = pst.st_size, True, pre
    with open(local, "rb") as src:
        src.seek(offset)
        with sftp.open(part, "ab" if offset else "wb") as dst:
            if hasattr(dst, "set_pipelined"):
                dst.set_pipelined(True)      # paramiko: 不逐块等 ACK, 吞吐接近 scp
            while True:
                b = src.read(CHUNK)
                if not b:
                    break
                dst.write(b)
                full.update(b)
    want = full.hexdigest()
    got, how = remote_sha256(sftp, part, run)
    if got != want:
        _safe_remove(sftp, part)
        raise IntegrityError(f"上传后 sha256 不一致 (本地 {want[:12]}… / 远端 {got[:12]}…), "
                             "已删除远端半成品, 目标文件未改动; 请重试")
    _commit(sftp, part, path, backup, st)
    return {"path": path, "size": size, "sha256": want, "verify": how,
            "resumed_from": offset if resumed else 0}


def transfer_timeout(size: int) -> int:
    """按 1MB/s 保守估算传输超时 (下限 60s, 上限 2 小时)。"""
    return int(min(7200, max(60, 60 + size / (1024 * 1024))))
