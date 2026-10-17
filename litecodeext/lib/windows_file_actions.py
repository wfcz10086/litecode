"""Windows 节点文件传输执行端：list_dir、download（读块）、upload（写块）。

节点任务是无状态的（任务目录每次执行后删除），因此 download 每块按偏移重读源文件；
upload 的分块落到持久的 Downloads\\LiteCode 临时文件后追加，最后一块校验 sha256 再改名。
"""
import base64
import os
from pathlib import Path
import re
import time

try:                                   # 节点上组件平铺下发，按顶层名导入
    import windows_transfer as wt
except ImportError:                    # 服务端/测试环境按包导入
    from lib import windows_transfer as wt

LIST_LIMIT = 500


def downloads_dir():
    """%USERPROFILE%\\Downloads\\LiteCode，不存在则创建。"""
    home = os.environ.get('USERPROFILE') or os.environ.get('HOME') or str(Path.home())
    target = Path(home) / 'Downloads' / wt.DOWNLOAD_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def list_dir(args):
    args = args or {}
    raw = args.get('path', '')
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('必须提供目录 path')
    directory = Path(os.path.expandvars(raw)).expanduser()   # 支持 %USERPROFILE% 等 (真机 2026-10-17 模型常这么写)
    if not directory.exists():
        raise ValueError('目录不存在')
    if not directory.is_dir():
        raise ValueError('path 不是目录')
    entries, truncated = [], False
    for index, entry in enumerate(sorted(directory.iterdir(), key=lambda p: p.name.lower())):
        if index >= LIST_LIMIT:
            truncated = True
            break
        try:
            stat = entry.stat()
            is_dir = entry.is_dir()
            entries.append({'name': entry.name, 'is_dir': is_dir,
                            'size': 0 if is_dir else stat.st_size,
                            'modified': int(stat.st_mtime)})
        except OSError:
            continue
    return {'ok': True, 'state': 'list_dir', 'path': str(directory),
            'count': len(entries), 'truncated': truncated, 'entries': entries}


def download(args):
    """index 缺省→返回元数据(size/chunks/sha256/name)；给定 index→返回该块 base64。"""
    args = args or {}
    raw = args.get('path', '')
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('必须提供文件 path')
    source = Path(os.path.expandvars(raw)).expanduser()
    if not source.exists() or not source.is_file():
        raise ValueError('文件不存在')
    size = source.stat().st_size
    total = wt.chunk_count(size)   # 同时校验 ≤50MB
    index = args.get('index')
    if index is None:
        data = source.read_bytes()
        return {'ok': True, 'state': 'download_meta', 'name': source.name,
                'size': size, 'chunks': total, 'sha256': wt.sha256_hex(data)}
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < total:
        raise ValueError('index 超出范围')
    with source.open('rb') as handle:
        handle.seek(index * wt.CHUNK)
        block = handle.read(wt.CHUNK)
    return {'ok': True, 'state': 'download_chunk', 'index': index,
            'chunk': base64.b64encode(block).decode()}


def upload(args):
    """逐块写入 Downloads\\LiteCode 下的临时文件；最后一块校验整体 sha256 后改为唯一文件名。"""
    args = args or {}
    name = args.get('name', '')
    index = args.get('index')
    total = args.get('total')
    sha256 = args.get('sha256', '')
    payload = args.get('data', '')
    transfer_id = args.get('transfer_id', '')
    if not re.fullmatch(r'[a-f0-9]{8,64}', str(transfer_id)):
        raise ValueError('transfer_id 非法')
    for value in (index, total):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('index/total 必须为整数')
    if not 0 <= index < total or total < 1:
        raise ValueError('index/total 超出范围')
    if not re.fullmatch(r'[a-f0-9]{64}', str(sha256)):
        raise ValueError('sha256 非法')
    block = base64.b64decode(payload, validate=True) if payload else b''
    temp = downloads_dir() / ('.part-' + transfer_id)
    current = temp.stat().st_size if temp.exists() else 0
    expected_offset = index * wt.CHUNK
    if index == 0:
        temp.write_bytes(b'')   # 新传输从头开始，清掉可能的残留
        current = 0
    if current != expected_offset:
        temp.unlink(missing_ok=True)
        raise ValueError('分块乱序或上一传输残留，请重新开始上传')
    if len(block) > wt.CHUNK:
        temp.unlink(missing_ok=True)
        raise ValueError('单块超过上限')
    with temp.open('ab') as handle:
        handle.write(block)
    if temp.stat().st_size > wt.MAX_TRANSFER:
        temp.unlink(missing_ok=True)
        raise ValueError('累计超过 50MB 上限')
    if index < total - 1:
        return {'ok': True, 'state': 'upload_chunk', 'index': index, 'received': temp.stat().st_size}
    data = temp.read_bytes()
    if wt.sha256_hex(data) != sha256:
        temp.unlink(missing_ok=True)
        raise ValueError('整体 sha256 校验失败，上传已损坏')
    final = wt.unique_target(downloads_dir(), name)
    os.replace(temp, final)
    return {'ok': True, 'state': 'upload_done', 'path': str(final),
            'size': len(data), 'name': final.name, 'saved_at': int(time.time())}


def perform(action, folder, args):
    if action == 'list_dir':
        return list_dir(args)
    if action == 'download':
        return download(args)
    if action == 'upload':
        return upload(args)
    raise ValueError('不支持的文件动作')
