"""handlers/symbol_edit.py — locate_symbol, edit_symbol (基于 CALL_GRAPH.json)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import register


@register("locate_symbol")
async def h_locate_symbol(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    WORKSPACE = td.WORKSPACE
    _sym_name = args.get("name", "").strip()
    _narrow = args.get("filepath", "").strip()
    _depth = min(int(args.get("depth", 2)), 5)
    if not _sym_name:
        return "ERROR: name is required", None

    _graphs = sorted(WORKSPACE.rglob("CALL_GRAPH.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not _graphs:
        return "ERROR: no CALL_GRAPH.json found. Run project_init first.", None
    _graph_file = _graphs[0]
    try:
        _cg = json.loads(_graph_file.read_text())
    except Exception as e:
        return f"ERROR reading call graph: {e}", None

    matches = []
    for sym, info in _cg.items():
        if _narrow and _narrow not in info.get("file", ""):
            continue
        if sym == _sym_name:
            matches.insert(0, (sym, info))
        elif _sym_name.lower() in sym.lower():
            matches.append((sym, info))

    if not matches:
        _proj_root = _graph_file.parent
        try:
            import subprocess as _sp
            res = _sp.run(
                ["grep", "-rn", "--include=*", _sym_name, str(_proj_root)],
                capture_output=True, text=True, timeout=10
            )
            hits = res.stdout.strip().splitlines()[:10]
            if hits:
                return "[locate_symbol] not in manifest, grep results:\n" + "\n".join(hits), None
        except Exception:
            pass
        return f"[locate_symbol] '{_sym_name}' not found. Check spelling or run project_init.", None

    out_parts = []
    for sym, info in matches[:3]:
        fpath = _graph_file.parent / info["file"]
        lineno = info.get("line", 1)
        try:
            lines = fpath.read_text(errors="replace").splitlines()
            start = max(0, lineno - 2)
            end = min(len(lines), lineno + 18)
            ctx = "\n".join(f"{start+i+1:4d}  {l}" for i, l in enumerate(lines[start:end]))
        except Exception:
            ctx = "(could not read file)"

        def _chain(s, cg, direction, visited, depth_left):
            if depth_left <= 0 or s in visited:
                return []
            visited.add(s)
            peers = cg.get(s, {}).get(direction, [])[:4]
            result = []
            for p in peers:
                pinfo = cg.get(p, {})
                result.append(f"{p} @ {pinfo.get('file','?')}:{pinfo.get('line','?')}")
                result += ["  " + x for x in _chain(p, cg, direction, visited, depth_left - 1)]
            return result

        callers = _chain(sym, _cg, "callers", set(), _depth)
        callees = _chain(sym, _cg, "callees", set(), _depth)

        part = f"[{sym}]  {info['file']} L{lineno}\n"
        part += f"Context:\n{ctx}\n"
        if callers:
            part += "\nCallers (who calls this):\n  " + "\n  ".join(callers)
        if callees:
            part += "\nCallees (what this calls):\n  " + "\n  ".join(callees)
        out_parts.append(part)

    return "\n\n".join(out_parts), None


@register("edit_symbol")
async def h_edit_symbol(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    WORKSPACE = td.WORKSPACE
    _filepath = args.get("filepath", "").strip()
    _symbol_name = args.get("symbol_name", "").strip()
    _new_code = args.get("new_code", "")
    if not _filepath or not _symbol_name or not _new_code:
        return "ERROR: filepath, symbol_name, new_code all required", None

    _fpath = Path(_filepath) if Path(_filepath).is_absolute() else WORKSPACE / _filepath
    if not _fpath.exists():
        return f"ERROR: file not found: {_fpath}", None

    ext = _fpath.suffix.lower()
    try:
        src_lines = _fpath.read_text(errors="replace").splitlines(keepends=True)
    except Exception as e:
        return f"ERROR reading file: {e}", None

    _HEAD_PATS = {
        ".py":   [rf"^(\s*)(async\s+def|def|class)\s+{re.escape(_symbol_name)}\b"],
        ".js":   [rf"^(\s*)(?:export\s+)?(?:async\s+)?function\s+{re.escape(_symbol_name)}\b",
                  rf"^(\s*)(?:export\s+)?(?:const|let|var)\s+{re.escape(_symbol_name)}\s*="],
        ".ts":   [rf"^(\s*)(?:export\s+)?(?:async\s+)?function\s+{re.escape(_symbol_name)}\b",
                  rf"^(\s*)(?:export\s+)?(?:abstract\s+)?class\s+{re.escape(_symbol_name)}\b",
                  rf"^(\s*)(?:export\s+)?(?:const|let|var)\s+{re.escape(_symbol_name)}\s*=",
                  rf"^(\s*)(?:export\s+)?(?:interface|type|enum)\s+{re.escape(_symbol_name)}\b"],
        ".go":   [rf"^(\s*)func\s+(?:\(\w+\s+\*?\w+\)\s+)?{re.escape(_symbol_name)}\s*\(",
                  rf"^(\s*)type\s+{re.escape(_symbol_name)}\s+"],
        ".rs":   [rf"^(\s*)(?:pub(?:\(\w+\))?\s+)?(?:async\s+)?fn\s+{re.escape(_symbol_name)}\b",
                  rf"^(\s*)(?:pub\s+)?(?:struct|enum|trait)\s+{re.escape(_symbol_name)}\b"],
        ".java": [rf"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+{re.escape(_symbol_name)}\s*\(",
                  rf"(?:public|private|protected|\s)*class\s+{re.escape(_symbol_name)}\b"],
        ".c":    [rf"^[\w\s\*]+\s+{re.escape(_symbol_name)}\s*\([^;]"],
        ".cpp":  [rf"^[\w\s\*:]+\s+{re.escape(_symbol_name)}\s*\([^;]",
                  rf"^class\s+{re.escape(_symbol_name)}\b"],
        ".rb":   [rf"^(\s*)def\s+{re.escape(_symbol_name)}\b",
                  rf"^(\s*)class\s+{re.escape(_symbol_name)}\b"],
        ".php":  [rf"^(\s*)(?:public|private|protected|static|\s)*function\s+{re.escape(_symbol_name)}\b"],
        ".sh":   [rf"^(?:function\s+)?{re.escape(_symbol_name)}\s*\(\s*\)"],
        ".lua":  [rf"^(?:local\s+)?function\s+{re.escape(_symbol_name)}\b",
                  rf"^{re.escape(_symbol_name)}\s*=\s*function"],
    }
    pats = _HEAD_PATS.get(ext, [rf"^[\w\s]*{re.escape(_symbol_name)}[\w\s]*[{{(:]"])

    start_idx = None
    base_indent = ""
    for i, line in enumerate(src_lines):
        for pat in pats:
            if re.search(pat, line):
                start_idx = i
                m = re.match(r"(\s*)", line)
                base_indent = m.group(1) if m else ""
                break
        if start_idx is not None:
            break

    if start_idx is None:
        return (
            f"ERROR: symbol '{_symbol_name}' not found in {_fpath.name}. "
            f"Use locate_symbol first to confirm exact name and file."
        ), None

    end_idx = start_idx + 1
    if ext in (".py", ".rb"):
        for i in range(start_idx + 1, len(src_lines)):
            stripped = src_lines[i].rstrip()
            if not stripped:
                end_idx = i + 1
                continue
            curr_indent = len(src_lines[i]) - len(src_lines[i].lstrip())
            base_len = len(base_indent)
            if curr_indent <= base_len and stripped.strip():
                break
            end_idx = i + 1
    elif ext in (".sh", ".lua"):
        _END_KW = {"end", "fi", "done", "esac"}
        brace_depth = 0
        for i in range(start_idx, len(src_lines)):
            brace_depth += src_lines[i].count("{") - src_lines[i].count("}")
            stripped = src_lines[i].strip().split()[0] if src_lines[i].strip() else ""
            if i > start_idx and (stripped in _END_KW or (brace_depth <= 0 and "{" in "".join(src_lines[start_idx:i + 1]))):
                end_idx = i + 1
                break
    else:
        brace_depth = 0
        found_open = False
        for i in range(start_idx, len(src_lines)):
            brace_depth += src_lines[i].count("{") - src_lines[i].count("}")
            if "{" in src_lines[i]:
                found_open = True
            if found_open and brace_depth <= 0:
                end_idx = i + 1
                break

    old_block = "".join(src_lines[start_idx:end_idx])
    new_block = _new_code.rstrip("\n") + "\n"

    new_src_lines = src_lines[:start_idx] + [new_block] + src_lines[end_idx:]
    new_src = "".join(new_src_lines)

    _bak_dir = WORKSPACE / ".openclaw_checkpoints"
    _bak_dir.mkdir(exist_ok=True)
    _bak = _bak_dir / f"{_fpath.name}.{int(time.time())}.bak"
    _bak.write_text(_fpath.read_text(errors="replace"))

    _fpath.write_text(new_src)

    import subprocess as _sp
    _SYNTAX_CMDS = {
        ".py":   ["python3", "-m", "py_compile", str(_fpath)],
        ".js":   ["node", "--check", str(_fpath)],
        ".ts":   ["npx", "--yes", "tsc", "--noEmit", "--strict", str(_fpath)],
        ".go":   ["go", "build", str(_fpath)],
        ".rs":   ["rustc", "--edition", "2021", "--emit=metadata", "-o", "/dev/null", str(_fpath)],
        ".rb":   ["ruby", "-c", str(_fpath)],
        ".php":  ["php", "-l", str(_fpath)],
        ".sh":   ["bash", "-n", str(_fpath)],
        ".lua":  ["luac", "-p", str(_fpath)],
    }
    syntax_result = ""
    _scmd = _SYNTAX_CMDS.get(ext)
    if _scmd:
        try:
            sr = _sp.run(_scmd, capture_output=True, text=True, timeout=15)
            if sr.returncode != 0:
                err = (sr.stdout + sr.stderr).strip()[:300]
                _fpath.write_text(_bak.read_text())
                return (
                    f"ERROR: syntax check failed after edit, rolled back.\n"
                    f"Error: {err}\n"
                    f"Hint: fix the new_code and retry."
                ), None
            syntax_result = " [syntax OK]"
        except FileNotFoundError:
            syntax_result = " [syntax check skipped: tool not found]"
        except Exception as ex:
            syntax_result = f" [syntax check error: {ex}]"

    lines_old = old_block.count("\n")
    lines_new = new_block.count("\n")
    _graphs = sorted(WORKSPACE.rglob("CALL_GRAPH.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if _graphs:
        try:
            _cg = json.loads(_graphs[0].read_text())
            offset = lines_new - lines_old
            if offset != 0:
                rel = str(_fpath.relative_to(_graphs[0].parent))
                for sym, info in _cg.items():
                    if info.get("file") == rel and info.get("line", 0) > end_idx:
                        info["line"] += offset
            _graphs[0].write_text(json.dumps(_cg, ensure_ascii=False, indent=2))
        except Exception:
            pass

    return (
        f"[edit_symbol] '{_symbol_name}' replaced in {_fpath.name}{syntax_result}\n"
        f"Lines: {start_idx+1}-{end_idx} ({lines_old} lines) -> {lines_new} lines\n"
        f"Backup: {_bak.name}"
    ), None
