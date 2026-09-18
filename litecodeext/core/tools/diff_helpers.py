"""core/tools/diff_helpers.py — unified diff + patch preview helpers."""
from __future__ import annotations

import difflib as _difflib


def _make_diff(old: str, new: str, filepath: str, context: int = 4) -> str:
    """生成 unified diff，供客户端渲染。"""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(_difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
        n=context,
    ))
    if not diff:
        return ""
    # 只保留前 200 行（避免超大 diff 塞满 SSE）
    return "".join(diff[:200])


def _patch_context(new_txt: str, old_str: str, new_str: str, filepath: str, ctx: int = 3) -> str:
    """返回 patch 修改点周围的行（带行号），让模型看到实际变更结果。"""
    lines = new_txt.splitlines()
    # 找 new_str 在新文件中的起始行
    new_str_first = (new_str or "").splitlines()[0] if new_str else ""
    hit_line = None
    if new_str_first:
        for i, l in enumerate(lines):
            if new_str_first in l:
                hit_line = i
                break
    if hit_line is None:
        # fallback: 返回前30行
        preview = lines[:30]
        numbered = "\n".join(f"{i+1:4d}\t{l}" for i, l in enumerate(preview))
        suffix = f"\n... ({len(lines)-30} more lines)" if len(lines) > 30 else ""
        return f"Patched {filepath} ({len(lines)} lines total)\n{numbered}{suffix}"
    start = max(0, hit_line - ctx)
    end   = min(len(lines), hit_line + len((new_str or "").splitlines()) + ctx)
    numbered = "\n".join(f"{start+i+1:4d}\t{l}" for i, l in enumerate(lines[start:end]))
    return (
        f"Patched {filepath} — showing lines {start+1}-{end} of {len(lines)}:\n"
        f"{numbered}"
    )
