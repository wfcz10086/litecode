"""handlers/file_io.py — read_file, write_file, patch_file, apply_blocks, tail_log.

因这些 handler 依赖 tool_dispatch 模块级 state (WORKSPACE / _read_cache /
_save_checkpoint / _add_artifact / CONTEXT_WINDOW / log 等), 用惰性 import
在函数体内引用, 避免循环 import.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from . import register


@register("read_file")
async def h_read_file(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    p = Path(args["filepath"])
    if not p.is_absolute():
        for base in [td.WORKSPACE, td.BASE]:
            c = base / args["filepath"]
            if c.exists():
                p = c
                break
    if not p.exists():
        return f"ERROR: not found: {args['filepath']}", None
    try:
        _fsize = p.stat().st_size
    except Exception:
        _fsize = 0
    if _fsize > td.READ_FILE_MAX_BYTES:
        _mb = _fsize / (1024 * 1024)
        return (
            f"ERROR: file too large ({_mb:.1f} MB > 5 MB). "
            "Use lines='1:200' for partial read or execute_shell with sed/grep."
        ), None
    if p.suffix.lower() in td._READ_FILE_BINARY_EXTS:
        return (
            f"ERROR: binary file extension '{p.suffix.lower()}' not supported by read_file. "
            "Use execute_shell to inspect."
        ), None
    lines_arg = args.get("lines")
    if not lines_arg:
        try:
            mtime = p.stat().st_mtime
            cached = td._read_cache.get(str(p))
            if cached and cached[0] == mtime:
                return f"[unchanged since last read, {cached[1]} lines, {cached[2]} chars]", None
        except Exception:
            pass
    try:
        _chunk = p.read_bytes()[:8192]
        if 0 in _chunk:
            return (
                "ERROR: file appears to be binary (contains NUL bytes). "
                "Use execute_shell with file/xxd/strings."
            ), None
    except Exception:
        pass
    raw = p.read_text(errors="replace")
    try:
        td._read_cache[str(p)] = (p.stat().st_mtime, len(raw.splitlines()), len(raw))
    except Exception:
        pass
    all_lines = raw.splitlines()
    total_lines = len(all_lines)
    if lines_arg:
        sep = ":" if ":" in lines_arg else "-"
        parts = lines_arg.split(sep)
        start = int(parts[0]) - 1 if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] not in ("", "-1") else total_lines
    else:
        start, end = 0, total_lines
    MAX_LINES_FULL = 200
    if not lines_arg and total_lines > MAX_LINES_FULL:
        header = (
            f"[FILE TOO LARGE: {total_lines} lines total. Showing lines 1-{MAX_LINES_FULL}. "
            f"Use lines='start:end' to read specific sections, "
            f"or use execute_shell with grep/sed to locate relevant lines first.\n"
            f"---\n"
        )
        slice_lines = all_lines[:MAX_LINES_FULL]
        numbered = "\n".join(f"{i+1:4d}\t{l}" for i, l in enumerate(slice_lines))
        return header + numbered, None
    slice_lines = all_lines[start:end]
    numbered = "\n".join(f"{start+i+1:4d}\t{l}" for i, l in enumerate(slice_lines))
    return numbered[:20000], None


@register("write_file")
async def h_write_file(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    log = td.log
    WORKSPACE = td.WORKSPACE
    CONTEXT_WINDOW = td.CONTEXT_WINDOW

    if "filepath" not in args:
        import re as _re_wf
        _raw = str(args)
        _fp_match = _re_wf.search(r'"filepath"\s*:\s*"([^"]+)"', _raw)
        if _fp_match:
            args["filepath"] = _fp_match.group(1)
            log.warning(f"  [FIX] write_file filepath recovered from truncated JSON: {args['filepath']}")
        elif "content" in args:
            import hashlib as _hl
            _ct = args["content"][:200]
            _ext = ".py" if _ct.lstrip().startswith(("import ", "from ", "def ", "class ", "#!/")) else \
                   ".sh" if _ct.lstrip().startswith(("#!/bin/", "set -")) else \
                   ".md" if _ct.lstrip().startswith(("#", "---")) else \
                   ".json" if _ct.lstrip().startswith(("{", "[")) else ".txt"
            _hash = _hl.md5(_ct.encode()).hexdigest()[:8]
            args["filepath"] = str(WORKSPACE / f"recovered_{_hash}{_ext}")
            log.warning(f"  [FIX] write_file filepath auto-generated: {args['filepath']}")
        else:
            return ("ERROR: write_file missing required arg 'filepath' "
                    "(tool_call JSON truncated — likely caused by thinking tokens consuming max_tokens)"), None
    if "content" not in args:
        return "ERROR: write_file missing required arg 'content' (tool call JSON may have been truncated)", None
    raw_content = args["content"]
    _fp_ext = Path(args.get("filepath", "")).suffix.lower()
    _is_markup  = _fp_ext in (".html", ".htm", ".css", ".jinja", ".jinja2", ".j2", ".svg", ".xml")
    _is_code    = _fp_ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp",
                               ".rb", ".php", ".sh", ".lua", ".swift", ".kt", ".cs", ".r",
                               ".scala", ".ex", ".exs", ".hs", ".ml", ".clj", ".vim")
    _line_count = raw_content.count("\n") + (1 if raw_content and not raw_content.endswith("\n") else 0)
    _char_count = len(raw_content)

    _CHARS_PER_TOKEN = 4
    _FILE_BUDGET = int(CONTEXT_WINDOW * _CHARS_PER_TOKEN * 0.15)
    _FILE_HARD_LIMIT = min(_FILE_BUDGET, 60000)
    _FILE_WARN_LIMIT = int(_FILE_HARD_LIMIT * 0.6)

    if _is_code and _char_count > _FILE_HARD_LIMIT:
        _fname = Path(args.get("filepath", "file")).name
        _DOMAIN_MAP = {
            "network":  {"aiohttp","httpx","requests","urllib","socket","websockets","fastapi","starlette","flask","django","tornado"},
            "database": {"sqlite3","sqlalchemy","pymongo","redis","psycopg2","motor","aiomysql","aiosqlite"},
            "filesystem":{"pathlib","os","shutil","tempfile","glob","fnmatch","zipfile","tarfile"},
            "crypto":   {"hashlib","hmac","secrets","cryptography","jwt","bcrypt"},
            "data":     {"json","yaml","toml","csv","xml","pydantic","dataclasses","marshmallow"},
            "process":  {"subprocess","multiprocessing","threading","concurrent","asyncio"},
            "ui":       {"tkinter","PyQt5","PyQt6","wx","kivy","textual","rich","click","typer"},
        }
        _import_domains = set()
        for _line in raw_content.splitlines()[:60]:
            _ls = _line.strip()
            if not (_ls.startswith("import ") or _ls.startswith("from ")):
                continue
            _pkg = _ls.split()[1].split(".")[0]
            for _domain, _pkgs in _DOMAIN_MAP.items():
                if _pkg in _pkgs:
                    _import_domains.add(_domain)
        _split_hint = ""
        if len(_import_domains) >= 3:
            _split_hint = (
                f"\nThis file mixes {len(_import_domains)} domains: {', '.join(_import_domains)}."
                f"\nSplit by domain boundary — each file should import from one domain."
            )
        else:
            _split_hint = (
                f"\nFile has single domain ({', '.join(_import_domains) or 'unknown'}) but is too large."
                f"\nSplit by sub-responsibility: init/core/utils/api."
            )
        _limit_lines = _FILE_HARD_LIMIT // 50
        return (
            f"ERROR: {_fname} is {_char_count} chars ({_line_count} lines), "
            f"exceeds {_FILE_HARD_LIMIT} char limit "
            f"(= context_window {CONTEXT_WINDOW} * 15%).{_split_hint}"
            f"\nTarget: each file <= {_limit_lines} lines / {_FILE_HARD_LIMIT} chars."
        ), None

    _warn_msg = ""
    if _is_code and _char_count > _FILE_WARN_LIMIT:
        _warn_lines = _FILE_WARN_LIMIT // 50
        _warn_msg = (
            f"\n[ARCH-HINT] {_char_count} chars is large (>{_warn_lines} lines equiv). "
            f"Consider splitting if this file has mixed responsibilities."
        )

    if _is_markup:
        _too_large = len(raw_content) > 60000
        _last = raw_content.rstrip().splitlines()[-1] if raw_content.strip() else ""
        _trunc = raw_content.endswith("\\") or (
            _fp_ext in (".html", ".htm") and
            not any(_last.endswith(t) for t in ("</html>", "/>", ">", "*/", ""))
        )
        if _too_large or (_trunc and len(raw_content) > 3000):
            return (
                f"ERROR: markup file too large ({len(raw_content)} chars). "
                f"Split JS into separate .js file, CSS into .css file, "
                f"then reference them via <script src> and <link rel=stylesheet>."
            ), None
    elif _is_code:
        _trunc = (
            raw_content.endswith("\\") or
            (raw_content.count("{") > raw_content.count("}") + 2) or
            (raw_content.count("(") > raw_content.count(")") + 3)
        )
        if _trunc and len(raw_content) > 3000:
            return (
                f"ERROR: content appears truncated ({_line_count} lines). "
                f"Rewrite the complete function/class, or use patch_file to append."
            ), None

    p = Path(args["filepath"])
    # [rel-path visibility 2026-08-30] 相对路径会被静默拼进 WORKSPACE。
    # 实测事故: 模型在一个任务里混用绝对/相对路径, 代码被劈成两半 ——
    # /tmp/yijing-go/ 与 /tmp/litecode_workspace/yijing-go/ 各有一部分,
    # go build 必然失败, 而报错是"undefined: XXX", 完全指不到路径问题,
    # 于是模型反复 rm 重写, 越修越乱。
    # 返回值里本来就有解析后的绝对路径, 但要模型自己比对"我传的 vs 返回的"
    # 才能发现不一致, 太隐晦。这里改成**显式说出重解析这件事**。
    _rel_note = ""
    if not p.is_absolute():
        p = WORKSPACE / args["filepath"]
        _rel_note = (
            f"\n[PATH] 你传的是相对路径 {args['filepath']!r}, 已解析为 {p}"
            f"\n  若你想写到别处, 请传绝对路径。同一项目请始终用同一种写法,"
            f"\n  混用会让文件散落在两个目录, 编译/导入报错却指不到根因。"
        )
    old_content = p.read_text(errors="replace") if p.exists() else ""
    if p.exists() and old_content:
        td._save_checkpoint(p, old_content)
    p.parent.mkdir(parents=True, exist_ok=True)
    new_content = args["content"]
    p.write_text(new_content)
    td._read_cache.pop(str(p), None)
    _syntax_warn = ""
    if _fp_ext == ".py" and len(new_content) > 50:
        try:
            compile(new_content, str(p), "exec")
        except SyntaxError as _se:
            _syntax_warn = (
                f"\n\n[SYNTAX_ERROR] {p.name} 第{_se.lineno}行: {_se.msg}"
                f"\n  问题行: {_se.text.strip() if _se.text else '(unknown)'}"
                f"\n  常见原因: f-string 嵌套引号 / 缩进错误 / 括号不匹配"
                f"\n  建议: 避免在 f-string 中用同类引号, 改用 .format() 或 str 拼接"
                f"\n  [SYSTEM: 此文件已写入但有语法错误, 必须立即修复再运行]"
            )
    diff = td._make_diff(old_content, new_content, str(p)) if old_content != new_content else ""
    if sid:
        td._add_artifact(sid, {
            "filepath": str(p), "op": "write" if not old_content else "overwrite",
            "size": len(new_content), "ts": time.time()
        })
    lines_written = new_content.splitlines()
    total = len(lines_written)
    _cn_hint = ""
    if _fp_ext in (".md", ".txt") and len(new_content) > 500:
        import re as _re_cn
        _cn_count = len(_re_cn.findall(r'[一-鿿]', new_content))
        if _cn_count > 100:
            _cn_hint = f" (中文{_cn_count}字)"
    result_str = (f"Written {total} lines ({len(new_content)} chars{_cn_hint}) -> {p}"
                  + _rel_note + _warn_msg + _syntax_warn)
    try:
        from progress_tracker import auto_update_state as _aus
        _pinfo = _aus(str(p), "write")
        if _pinfo and _pinfo.get("action") == "updated":
            result_str += f"\n[progress] state.json current_chapter: {_pinfo['prev']} → {_pinfo['new']}"
    except Exception:
        pass
    try:
        result_str += td._check_batch_write_guard(sid or "", str(p), not bool(old_content))
    except Exception:
        pass
    return result_str, diff or None


@register("patch_file")
async def h_patch_file(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    WORKSPACE = td.WORKSPACE
    p = Path(args["filepath"])
    _rel_note = ""
    if not p.is_absolute():
        p = WORKSPACE / args["filepath"]
        # [rel-path visibility 2026-08-30] 同 h_write_file
        _rel_note = (f"\n[PATH] 相对路径 {args['filepath']!r} 已解析为 {p}"
                     f"; 想写到别处请传绝对路径, 同一项目别混用两种写法")
    if args.get("append"):
        open(p, "a").write(args.get("new_str", ""))
        td._read_cache.pop(str(p), None)
        return f"Appended -> {p}", None
    if not p.exists():
        return f"ERROR: not found: {p}", None
    old_txt = p.read_text()
    old_str = args.get("old_str", "")
    n = old_txt.count(old_str)
    if n == 0:
        _sample = "\n".join(old_txt.splitlines()[:80])
        _old_preview = (old_str or "")[:60].replace("\n", "\\n")
        _hint = (
            f"ERROR: pattern not found in {p.name}\n"
            f"[要查找 60c 预览]: {_old_preview}\n"
            f"[文件前 80 行]:\n{_sample}\n"
            f"[修复建议] 1) 先 read_file 看最新内容; 2) old_str 要逐字复制, "
            f"空白/换行/引号都要对; 3) 若文件太大或仅改小段, 用 write_file 整段覆盖 "
            f"更稳。"
        )
        return _hint, None
    if n > 1:
        return (
            f"ERROR: {n} matches, must be unique — "
            f"把 old_str 上下文加长 2-3 行让其唯一, 或改用 write_file 整块覆盖。",
            None
        )
    td._save_checkpoint(p, old_txt)
    new_txt = old_txt.replace(old_str, args.get("new_str", ""), 1)
    p.write_text(new_txt)
    td._read_cache.pop(str(p), None)
    diff = td._make_diff(old_txt, new_txt, str(p))
    if sid:
        td._add_artifact(sid, {
            "filepath": str(p), "op": "patch",
            "size": len(new_txt), "ts": time.time()
        })
    context_result = td._patch_context(new_txt, old_str, args.get("new_str", ""), str(p))
    try:
        from progress_tracker import auto_update_state as _aus
        _pinfo = _aus(str(p), "patch")
        if _pinfo and _pinfo.get("action") == "updated":
            context_result += f"\n[progress] state.json current_chapter: {_pinfo['prev']} → {_pinfo['new']}"
    except Exception:
        pass
    return context_result, diff or None


@register("apply_blocks")
async def h_apply_blocks(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    WORKSPACE = td.WORKSPACE
    blocks_text = args.get("blocks", "").strip()
    if not blocks_text:
        return "ERROR: apply_blocks requires 'blocks' arg (Aider-style SEARCH/REPLACE)", None
    import re as _re
    pattern = _re.compile(
        r'^([^\n]+)\n<{7}\s*SEARCH\n(.*?)\n={7}\n(.*?)\n>{7}\s*REPLACE',
        _re.MULTILINE | _re.DOTALL
    )
    matches = pattern.findall(blocks_text)
    if not matches:
        return "ERROR: no valid Aider blocks parsed (expected `path\\n<<<<<<< SEARCH\\n...\\n=======\\n...\\n>>>>>>> REPLACE`)", None
    results = []
    failed = []
    rel_notes = []   # [rel-path visibility] 记录被重解析的相对路径
    for i, (raw_path, old_str, new_str) in enumerate(matches, 1):
        try:
            p = Path(raw_path.strip())
            if not p.is_absolute():
                p = WORKSPACE / raw_path.strip()
                # [rel-path visibility 2026-08-30] 同 h_write_file, 见那里的事故说明
                rel_notes.append(f"{raw_path.strip()} -> {p}")
            if not p.exists():
                failed.append(f"#{i} {raw_path}: file not found")
                continue
            old_txt = p.read_text()
            n = old_txt.count(old_str)
            if n == 0:
                failed.append(f"#{i} {raw_path}: SEARCH not found ({len(old_str)}c)")
                continue
            if n > 1:
                failed.append(f"#{i} {raw_path}: SEARCH matches {n} times, expected unique")
                continue
            td._save_checkpoint(p, old_txt)
            new_txt = old_txt.replace(old_str, new_str, 1)
            p.write_text(new_txt)
            td._read_cache.pop(str(p), None)
            if sid:
                td._add_artifact(sid, {"filepath": str(p), "op": "apply_block", "size": len(new_txt), "ts": time.time()})
            results.append(f"#{i} {raw_path}: applied (-{len(old_str)}c +{len(new_str)}c)")
        except Exception as e:
            failed.append(f"#{i} {raw_path}: {e}")
    summary = [f"Applied {len(results)}/{len(matches)} blocks"]
    if rel_notes:
        summary.append("[PATH] 以下相对路径已解析到 WORKSPACE 下, 想写别处请传绝对路径:")
        summary.extend("  " + n for n in rel_notes)
    summary.extend(results)
    if failed:
        summary.append(f"\nFailed {len(failed)} blocks:")
        summary.extend(failed)
        summary.append("\n[修复建议] 失败块用 read_file 确认最新内容, 重新提交未应用的块")
    return "\n".join(summary), None


@register("tail_log")
async def h_tail_log(sid: str, args: dict) -> tuple[str, Any]:
    _tl_path = args.get("path", "").strip()
    _tl_lines = max(1, min(500, int(args.get("lines", 30))))
    _tl_follow = max(0, min(30, int(args.get("follow_seconds", 0))))
    if not _tl_path:
        return "ERROR: tail_log 需要 path 参数", None
    _tp = Path(_tl_path)
    if not _tp.exists():
        return f"ERROR: 文件不存在: {_tl_path}", None
    if not _tp.is_file():
        return f"ERROR: 不是文件: {_tl_path}", None
    try:
        if _tl_follow > 0:
            loop = asyncio.get_event_loop()  # noqa: F841
            start_pos = max(0, _tp.stat().st_size - 8192)
            async def _tail_follow():
                out = []
                with open(_tp, "r", errors="replace") as f:
                    f.seek(start_pos)
                    for _ in range(_tl_follow * 2):
                        chunk = f.read()
                        if chunk:
                            out.append(chunk)
                        await asyncio.sleep(0.5)
                return "".join(out)
            data = await asyncio.wait_for(_tail_follow(), timeout=_tl_follow + 5)
            tail_lines = data.splitlines()[-_tl_lines:]
        else:
            lines_buf = []
            with open(_tp, "r", errors="replace") as f:
                sz = _tp.stat().st_size
                if sz > 5 * 1024 * 1024:
                    f.seek(sz - 5 * 1024 * 1024)
                    f.readline()
                lines_buf = f.readlines()
            tail_lines = [l.rstrip("\n") for l in lines_buf[-_tl_lines:]]
        return (
            f"[tail -n {_tl_lines} {_tl_path}]\n" +
            "\n".join(tail_lines)
        ), None
    except Exception as _te:
        return f"ERROR tail_log: {_te}", None
