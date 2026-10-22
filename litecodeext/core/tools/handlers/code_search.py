"""handlers/code_search.py — find_files, search_code, find_symbol, get_tree."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import register


def _resolve_root(raw_path: str):
    import tool_dispatch as td
    root = Path(raw_path) if (raw_path and Path(raw_path).is_absolute()) else td.WORKSPACE
    if raw_path and not Path(raw_path).is_absolute():
        for _b in [td.WORKSPACE, td.BASE]:
            _c = _b / raw_path
            if _c.exists():
                root = _c
                break
    return root


@register("find_files")
async def h_find_files(sid: str, args: dict) -> tuple[str, Any]:
    pattern = args.get("pattern", "*")
    max_r = int(args.get("max_results", 50))
    root = _resolve_root(args.get("path", ""))
    SKIP_DIRS = {'__pycache__','node_modules','.git','.venv','venv',
                 'dist','build','.mypy_cache','.pytest_cache',
                 '.next','target','vendor','.cache','.tox'}
    matches = []
    try:
        for f in root.rglob(pattern):
            if f.is_file():
                if any(part in SKIP_DIRS for part in f.parts):
                    continue
                try:    matches.append(str(f.relative_to(root)))
                except: matches.append(str(f))
                if len(matches) >= max_r:
                    break
    except Exception as e:
        return f"ERROR: {e}", None
    if not matches:
        return f"No files matching '{pattern}' in {root}", None
    return "\n".join(sorted(matches)) + f"\n\n({len(matches)} files found)", None


@register("search_code")
async def h_search_code(sid: str, args: dict) -> tuple[str, Any]:
    import re as _re2
    query = args.get("query", "")
    ext = args.get("ext", "")
    max_r = int(args.get("max_results", 40))
    root = _resolve_root(args.get("path", ""))
    try:
        pat = _re2.compile(query, _re2.IGNORECASE)
    except _re2.error:
        pat = _re2.compile(_re2.escape(query), _re2.IGNORECASE)
    SKIP_EXTS = {'.pyc','.jpg','.jpeg','.png','.gif','.webp',
                 '.zip','.tar','.gz','.bz2','.xz','.pdf',
                 '.exe','.bin','.so','.dylib','.whl'}
    SKIP_DIRS = {'__pycache__','node_modules','.git','.venv','venv',
                 'dist','build','.mypy_cache','.pytest_cache'}
    # [large-file 2026-10-21] 原来每个文件 read_text().splitlines() 整份进内存且在事件循环里同步跑,
    # 目录里有 1GB 日志就整份吃进来并卡住全站。改为: 线程池里逐行流式扫, 总耗时 20s 封顶并说明。
    import time as _time
    budget = 20.0

    def _scan():
        results, timed_out, t0 = [], False, _time.monotonic()
        files = [root] if root.is_file() else root.rglob("*")
        for f in files:
            if _time.monotonic() - t0 > budget:
                timed_out = True
                break
            if not f.is_file():
                continue
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            if f.suffix in SKIP_EXTS:
                continue
            if ext and f.suffix != ext:
                continue
            try:
                with open(f, "r", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if pat.search(line):
                            try:    rel = str(f.relative_to(root)) if f != root else f.name
                            except: rel = str(f)
                            results.append(f"{rel}:{i}: {line.strip()[:120]}")
                            if len(results) >= max_r:
                                break
                        if not (i & 0xFFFF) and _time.monotonic() - t0 > budget:
                            timed_out = True
                            break
            except Exception:
                continue
            if len(results) >= max_r or timed_out:
                break
        return results, timed_out

    import asyncio as _asyncio
    try:
        results, timed_out = await _asyncio.to_thread(_scan)
    except Exception as e:
        return f"ERROR: {e}", None
    note = (f"\n[扫描超过 {int(budget)}s 提前停止, 结果可能不全; 大文件/大目录请缩小 path 或用 "
            f"execute_shell 跑 rg/grep -n]") if timed_out else ""
    if not results:
        return f"No matches for '{query}' in {root}" + note, None
    return "\n".join(results) + f"\n\n({len(results)} matches)" + note, None


@register("find_symbol")
async def h_find_symbol(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    sym_name = args.get("name", "").strip()
    if not sym_name:
        return "ERROR: find_symbol requires 'name' arg", None
    for base in [td.BASE, td.WORKSPACE]:
        idx_path = base / "SYMBOL_INDEX.json"
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text(errors="replace"))
                hits = idx.get("symbols", {}).get(sym_name, [])
                if hits:
                    lines = [f"Found {len(hits)} occurrence(s) of '{sym_name}':"]
                    for h in hits[:20]:
                        loc = f"{h['file']}:{h['line']}"
                        meta = h.get("params") or h.get("bases") or ""
                        lines.append(f"  [{h['type']}] {loc}  {meta}"[:200])
                    if len(hits) > 20:
                        lines.append(f"  ... ({len(hits)-20} more)")
                    return "\n".join(lines), None
            except Exception as e:
                return f"ERROR: SYMBOL_INDEX.json parse: {e}", None
    return f"Symbol '{sym_name}' not found in SYMBOL_INDEX.json (or index not built yet)", None


@register("get_tree")
async def h_get_tree(sid: str, args: dict) -> tuple[str, Any]:
    max_depth = int(args.get("max_depth", 3))
    show_hidden = bool(args.get("show_hidden", False))
    root = _resolve_root(args.get("path", ""))
    SKIP_DIRS = {'__pycache__','node_modules','.git','.venv','venv',
                 'dist','build','.mypy_cache','.pytest_cache'}
    lines = [str(root)]

    def _tree(p: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return
        visible = [
            item for item in items
            if (show_hidden or not item.name.startswith('.'))
            and item.name not in SKIP_DIRS
        ]
        for i, item in enumerate(visible):
            is_last = (i == len(visible) - 1)
            connector = '`-- ' if is_last else '|-- '
            lines.append(f"{prefix}{connector}{item.name}{'/' if item.is_dir() else ''}")
            if item.is_dir():
                _tree(item, prefix + ('    ' if is_last else '|   '), depth + 1)

    _tree(root, "", 1)
    return "\n".join(lines), None
