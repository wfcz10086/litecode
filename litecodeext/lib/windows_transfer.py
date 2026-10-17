"""Windows 节点文件传输的纯逻辑：分块、sha256 校验、文件名清洗与路径防穿越。

这里只放不依赖 Windows 专属库的纯函数，服务端与节点端共用，便于在 Linux 上直接测。
"""
import hashlib
from pathlib import Path, PureWindowsPath
import re

CHUNK = 4 * 1024 * 1024          # 单块原始字节上限，base64 后约 5.3MB，远低于请求体 24MB 上限
MAX_TRANSFER = 50 * 1024 * 1024  # 单个文件传输上限
DOWNLOAD_SUBDIR = 'LiteCode'     # 落地在 %USERPROFILE%\Downloads\LiteCode\


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def chunk_count(size):
    """给定字节数需要的块数；0 字节也算 1 块，保证空文件也能走同一流程。"""
    if size < 0:
        raise ValueError('文件大小非法')
    if size > MAX_TRANSFER:
        raise ValueError('文件超过 50MB 传输上限')
    return max(1, (size + CHUNK - 1) // CHUNK)


def slice_chunk(data, index):
    """取第 index 块原始字节（调用方负责 base64 编码后放入任务）。"""
    total = chunk_count(len(data))
    if not 0 <= index < total:
        raise ValueError('块序号超出范围')
    return data[index * CHUNK:(index + 1) * CHUNK]


def reassemble(chunks, expected_sha256):
    """按序号重组分块并校验整体 sha256；任何缺块、错序或哈希不符都拒绝。"""
    if not isinstance(chunks, dict) or not chunks:
        raise ValueError('没有可重组的分块')
    try:
        ordered = [chunks[i] for i in range(len(chunks))]
    except KeyError as exc:
        raise ValueError('分块缺失，无法重组') from exc
    data = b''.join(ordered)
    if len(data) > MAX_TRANSFER:
        raise ValueError('重组后超过 50MB 上限')
    if sha256_hex(data) != expected_sha256:
        raise ValueError('整体 sha256 校验失败，传输已损坏')
    return data


def sanitize_download_name(name):
    """把对端提供的文件名清洗为纯文件名：去目录、去穿越、限字符，空则兜底。"""
    if not isinstance(name, str):
        raise ValueError('文件名必须是字符串')
    # 同时按 POSIX 与 Windows 规则取末段，挡掉 ..\ 与 / 混用的穿越
    base = PureWindowsPath(Path(name).name).name
    base = base.replace('\x00', '')
    if base in ('', '.', '..') or set(base) <= {'.'}:
        return 'download.bin'
    # Windows 非法字符与控制字符一律换成下划线
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', base).strip(' .')
    base = base[:180] or 'download.bin'
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'com{i}' for i in range(1, 10)} | {f'lpt{i}' for i in range(1, 10)}
    stem = base.split('.')[0].lower()
    if stem in reserved:
        base = '_' + base
    return base


def unique_target(directory, name):
    """同名不覆盖：file.txt → file (1).txt → file (2).txt …"""
    directory = Path(directory)
    safe = sanitize_download_name(name)
    candidate = directory / safe
    if not candidate.exists():
        return candidate
    stem, dot, suffix = safe.partition('.')
    for index in range(1, 10000):
        alternate = directory / (f'{stem} ({index})' + (dot + suffix if dot else ''))
        if not alternate.exists():
            return alternate
    raise ValueError('同名文件过多，无法生成唯一文件名')


def resolve_within(base, candidate):
    """校验 candidate 落在 base 目录内（含 base 自身），返回解析后的绝对路径，否则拒绝。

    服务端用它确保上传源文件必须位于工作区内，杜绝把服务器任意文件发出去。
    """
    base = Path(base).resolve()
    target = Path(candidate).resolve()
    if target != base and base not in target.parents:
        raise ValueError('路径必须位于允许的工作区目录内')
    return target
