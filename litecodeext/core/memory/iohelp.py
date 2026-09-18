"""memory/io.py — 磁盘写入 + token 估算 + 压缩质量校验.

三个纯函数, 无副作用之外的状态:
  - estimate_tokens: 中英混合粗估
  - _atomic_write: 原子写盘 (tmp+rename), 失败退化直写
  - _validate_compressed: 压缩后 L1 内容合法性检查
"""
from __future__ import annotations

import json
import os as _os
from pathlib import Path

from .consts import L1_MAX_LINES


def estimate_tokens(messages: list) -> int:
    """粗估 token 数: CJK 字符按 1.5 token/字, ASCII 按 4 chars/token."""
    total_chars = 0
    for m in messages:
        text = json.dumps(m, ensure_ascii=False)
        cjk = sum(1 for c in text if '一' <= c <= '鿿' or '　' <= c <= 'ヿ')
        ascii_chars = len(text) - cjk
        total_chars += int(cjk * 1.5 + ascii_chars / 4)
    return total_chars


def _atomic_write(path: Path, content: str) -> None:
    """[v1.9 P38-c] 原子写盘 — 写到 .tmp 后 os.replace, 防止部分写入.
    [fix 2026-05-23] bind-mount / 并发场景下 tmp+rename 经常报 ENOENT
    (5+ session 都触发 'MEMORY.md.tmp -> MEMORY.md No such file or directory').
    退化: tmp 不可用就直写, 牺牲一点原子性换稳定性.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content)
        _os.replace(str(tmp), str(path))
        return
    except (FileNotFoundError, OSError) as _e:
        try:
            path.write_text(content)
        except Exception as _e2:
            import logging
            logging.getLogger("openclaw").debug(
                f"  [_atomic_write] both tmp and direct write failed for {path}: tmp={_e!r} direct={_e2!r}"
            )


def _validate_compressed(new_text: str, old_text: str) -> tuple:
    """返回 (is_valid, reason). 不通过 → 保留旧 L1."""
    new_len = len(new_text)
    old_len = len(old_text)
    new_lines = len(new_text.splitlines())

    if new_len < 200:
        return False, f"too short: {new_len}c < 200c"

    if old_len > 1000 and new_len < old_len * 0.05:
        return False, f"compressed too aggressively: {new_len}c < 5% of {old_len}c"

    if new_lines > L1_MAX_LINES * 3:
        return False, f"too many lines: {new_lines} > {L1_MAX_LINES * 3}"

    if "##" not in new_text:
        return False, "no ## sections found (likely model hallucinated free text)"

    for protected in ["Errors & Corrections", "Key References"]:
        if protected in old_text and protected not in new_text:
            return False, f"PROTECTED section '{protected}' missing in new compression"

    return True, "ok"
