"""large_file.py — 大文件按行分段读取 (read_file / tail_log / search_code 共用), 内存与文件大小无关。

- 行索引: 一次顺序扫描按 1MB 块数 b'\\n', 每块记 (字节偏移, 之前的换行数) 稀疏锚点;
  按 (路径, mtime_ns, size) 缓存。1GB 文件扫描约 1 秒, 之后任意行号 seek 到锚点再跳几行即可。
- 单行上限: 超长行 (压缩 JSON / minified JS) 只取前 line_cap 字节, 其余按块跳过, 不整行读进内存。
- 行定义只认 \\n (\\r\\n 去掉 \\r), 与 wc -l / sed -n 'a,bp' 一致, 末行无换行也算一行。
"""
from __future__ import annotations

import bisect
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

CHUNK = 1024 * 1024
_CACHE_MAX = 32
_cache: "OrderedDict[str, tuple[tuple[int, int], LineIndex]]" = OrderedDict()
_lock = threading.Lock()


@dataclass
class LineIndex:
    size: int
    total_lines: int
    offsets: list = field(default_factory=list)   # 锚点字节偏移
    nl_before: list = field(default_factory=list)  # 锚点之前的换行数
    last: bytes = b""                              # 最后一个字节 (判末行是否有换行)
    tail_fp: bytes = b""                           # 末尾 4KB 指纹, 增量续扫前核对


_INCR_MIN = 8 * 1024 * 1024    # 只有 >8MB 且只增长的文件 (日志) 走增量续扫, 小文件每次重建很便宜
_FP_BYTES = 4096


def _fingerprint(f, end: int) -> bytes:
    f.seek(max(0, end - _FP_BYTES))
    return f.read(min(end, _FP_BYTES))


def build_index(path: str, prev: LineIndex | None = None) -> LineIndex:
    """顺序扫描建索引; 给 prev (同一文件旧索引, 文件只追加) 时从 prev.size 续扫。"""
    with open(path, "rb") as f:
        if prev is not None:
            offsets, nl_before = list(prev.offsets), list(prev.nl_before)
            off, nl, last = prev.size, prev.nl_before[-1], prev.last
            f.seek(off)
        else:
            offsets, nl_before = [0], [0]
            off = nl = 0
            last = b""
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            nl += b.count(b"\n")
            off += len(b)
            last = b[-1:]
            offsets.append(off)
            nl_before.append(nl)
        fp = _fingerprint(f, off)
    total = nl + (1 if off > 0 and last != b"\n" else 0)
    return LineIndex(off, total, offsets, nl_before, last, fp)


def _can_extend(path: str, prev: LineIndex, size: int) -> bool:
    if prev.size < _INCR_MIN or size <= prev.size:
        return False
    try:
        with open(path, "rb") as f:
            return _fingerprint(f, prev.size) == prev.tail_fp
    except OSError:
        return False


def get_index(path: str) -> LineIndex:
    st = os.stat(path)
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    rp = os.path.realpath(path)
    with _lock:
        hit = _cache.get(rp)
        if hit and hit[0] == key:
            _cache.move_to_end(rp)
            return hit[1]
    prev = hit[1] if hit and hit[0][0] == st.st_ino and _can_extend(rp, hit[1], st.st_size) else None
    idx = build_index(rp, prev)
    with _lock:
        _cache[rp] = (key, idx)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return idx


def cached_index(path: str) -> LineIndex | None:
    """已缓存且仍有效 (或可增量续扫) 时返回索引, 否则 None (不触发全量扫描)。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _cache.get(os.path.realpath(path))
    if not hit or hit[0][0] != st.st_ino:
        return None
    if hit[0] == (st.st_ino, st.st_mtime_ns, st.st_size) or _can_extend(path, hit[1], st.st_size):
        return get_index(path)
    return None


def _skip_rest_of_line(f) -> tuple[int, bool]:
    """从当前位置跳到下一行行首 → (跳过字节数, 是否遇到换行); 按块读, 不吃整行进内存。"""
    n = 0
    while True:
        b = f.readline(CHUNK)
        if not b:
            return n, False
        n += len(b)
        if b.endswith(b"\n"):
            return n, True


def _read_line(f, cap: int) -> tuple[bytes, int] | None:
    """读一行: 返回 (前 cap 字节, 该行总字节数不含换行); EOF 返回 None。"""
    b = f.readline(cap + 1)
    if not b:
        return None
    if b.endswith(b"\n"):
        body = b[:-1]
        return body[:cap], len(body)
    if len(b) <= cap:          # 末行无换行
        return b, len(b)
    rest, nl = _skip_rest_of_line(f)
    return b[:cap], len(b) + rest - (1 if nl else 0)


def _seek_line(f, idx: LineIndex, line0: int) -> None:
    """把 f 定位到第 line0 行 (0 起) 行首。"""
    # 锚点 i 处于第 nl_before[i] 行内部 (或恰在其行首); readline 一次后到 nl_before[i]+1 行首。
    # 取最后一个满足 "起点行 <= line0" 的锚点。
    starts = idx.nl_before
    i = bisect.bisect_right(starts, line0 - 1) - 1
    i = max(i, 0)
    if idx.offsets[i] == 0:
        f.seek(0)
        cur = 0
    else:
        f.seek(idx.offsets[i])
        _skip_rest_of_line(f)
        cur = starts[i] + 1
    while cur < line0:
        if _skip_rest_of_line(f)[0] == 0:
            break
        cur += 1


def read_lines(path: str, start: int, count: int, line_cap: int = 2000,
               idx: LineIndex | None = None) -> list[tuple[int, str, int]]:
    """读第 start 行 (1 起) 开始的 count 行 → [(行号, 文本(截到 line_cap 字节), 原行字节数)]。"""
    idx = idx or get_index(path)
    if count <= 0 or start > idx.total_lines:
        return []
    start = max(1, start)
    out = []
    with open(path, "rb") as f:
        _seek_line(f, idx, start - 1)
        for k in range(count):
            r = _read_line(f, line_cap)
            if r is None:
                break
            raw, full = r
            if full == len(raw) and raw.endswith(b"\r"):
                raw = raw[:-1]
                full -= 1
            text = raw.decode("utf-8", errors="replace")
            if full > len(raw):
                text = text.rstrip("�")
            out.append((start + k, text, full))
    return out


TAIL_INDEX_LIMIT = 512 * 1024 * 1024   # 超过它且没现成索引时, tail 从文件尾倒着读, 不全量数行


def _tail_backward(path: str, n: int, line_cap: int, size: int) -> list[tuple[int, str, int]]:
    """从文件尾倒读到凑够 n 行 (行号未知记 0)。读到的字节上限 n*line_cap+1MB, 超长行只留尾部片段。"""
    budget = n * (line_cap + 2) + CHUNK
    with open(path, "rb") as f:
        pos, buf = size, b""
        while pos > 0 and buf.count(b"\n") <= n and len(buf) < budget:
            step = min(CHUNK, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    parts = buf.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
    if pos > 0 and parts:
        parts.pop(0)            # 第一段大概率是半行
    out = []
    for raw in parts[-n:]:
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        text = raw[:line_cap].decode("utf-8", errors="replace")
        if len(raw) > line_cap:
            text = text.rstrip("\ufffd")
        out.append((0, text, len(raw)))
    return out


def tail_lines(path: str, n: int, line_cap: int = 2000) -> tuple[list[tuple[int, str, int]], LineIndex | None]:
    """最后 n 行。小文件 / 已有索引 → 带行号与总行数; 超大且无索引 → 倒读, 返回 (rows, None)。"""
    idx = cached_index(path)
    if idx is None:
        size = os.stat(path).st_size
        if size > TAIL_INDEX_LIMIT:
            return _tail_backward(path, n, line_cap, size), None
        idx = get_index(path)
    start = max(1, idx.total_lines - n + 1)
    return read_lines(path, start, n, line_cap, idx), idx


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n}B" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return False
