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


# [large-file 2026-10-21] read_file 改为按行分段 (lib/large_file 稀疏行索引), 内存与文件大小无关:
# - 原来 >5MB 直接 ERROR, 连 lines='1:200' 都读不了 (提示语自相矛盾); 每次 read_bytes 整读判二进制;
# - 原来输出 20000 字符处静默截断, 模型不知道读到哪行、还剩多少;
# 现在任何大小都可分段读, 每次输出都写明 共 N 行 / 本次 a-b / 还剩 R 行 / 下一段的 lines 参数。
_RF_DEFAULT_LINES = 200      # 不给 lines 且文件较长时先给前 200 行
_RF_MAX_LINES = 500          # 单次最多返回行数
_RF_MAX_CHARS = 20000        # 单次输出字符预算 (约 5k token)
_RF_LINE_CAP = 2000          # 单行最多显示字节, 超长行截断并标注原长
_RF_DATA_EXTS = {".csv", ".tsv", ".jsonl", ".ndjson", ".parquet"}
_RF_LOG_EXTS = {".log", ".out", ".err"}
_RF_OFFICE_HINT = {
    ".xlsx": "Excel 别读进上下文: 用 execute_shell 跑 python (pandas.read_excel(nrows=20) 看结构 → 脚本里聚合/筛选, 只打印结果)",
    ".xls": "Excel 别读进上下文: 用 execute_shell 跑 python (pandas.read_excel(nrows=20) 看结构 → 脚本里聚合/筛选, 只打印结果)",
    ".pdf": "PDF 用 pdf / pdf-reading 技能 (pdftotext 分页抽文本), 别直接 read_file",
    ".docx": "Word 用 docx 技能 (python-docx / pandoc 转文本后再分段读)",
    ".pptx": "PPT 用 pptx 技能 (python-pptx 抽文本)",
}


def _rf_parse_range(lines_arg: str, total: int) -> tuple[int, int] | str:
    """'a:b' / 'a-b' / 'a:' / ':b' / '-N:' (最后 N 行) → (start, end) 1 起闭区间; 非法返回错误串。"""
    s = str(lines_arg).strip()
    try:
        if s.startswith("-") and s.endswith(":") and s[1:-1].isdigit():
            n = int(s[1:-1])
            return max(1, total - n + 1), total
        sep = ":" if ":" in s else "-"
        parts = s.split(sep)
        start = int(parts[0]) if parts[0].strip() else 1
        end = int(parts[1]) if len(parts) > 1 and parts[1].strip() not in ("", "-1") else total
    except ValueError:
        return f"ERROR: lines 参数格式不对: {lines_arg!r}; 用 '起:止' 如 '1:200', 末尾 N 行用 '-100:'"
    if start < 0:                       # '-50:-1' 之类: 按倒数处理
        start = max(1, total + start + 1)
    return max(1, start), min(end, total)


@register("read_file")
async def h_read_file(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    from lib import large_file as lf
    p = Path(args["filepath"])
    if not p.is_absolute():
        for base in [td.WORKSPACE, td.BASE]:
            c = base / args["filepath"]
            if c.exists():
                p = c
                break
    if not p.exists():
        return f"ERROR: not found: {args['filepath']}", None
    if p.is_dir():
        return f"ERROR: 是目录不是文件: {p} (用 get_tree / find_files 看目录)", None
    ext = p.suffix.lower()
    if ext in td._READ_FILE_BINARY_EXTS:
        hint = _RF_OFFICE_HINT.get(ext, "Use execute_shell (file/xxd/strings 或对应解析库) to inspect.")
        return f"ERROR: binary file extension '{ext}' not supported by read_file. {hint}", None
    lines_arg = args.get("lines")
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = None
    if not lines_arg:
        cached = td._read_cache.get(str(p))
        if cached and mtime is not None and cached[0] == mtime:
            return (f"[unchanged since last read, {cached[1]} lines, {cached[2]} bytes; "
                    f"要看其它部分用 lines='起:止' 或 '-100:']"), None
    if await asyncio.to_thread(lf.is_binary, str(p)):
        return ("ERROR: file appears to be binary (contains NUL bytes). "
                "Use execute_shell with file/xxd/strings."), None
    # 首次读大文件要扫一遍建行索引 (1GB ≈ 1s), 放线程池别卡事件循环
    idx = await asyncio.to_thread(lf.get_index, str(p))
    total = idx.total_lines
    if lines_arg:
        rng = _rf_parse_range(lines_arg, total)
        if isinstance(rng, str):
            return rng, None
        start, end = rng
        if total and start > total:
            return (f"[文件共 {total} 行, 请求的起始行 {start} 已超出末尾。"
                    f"末尾内容用 lines='-50:' 或 tail_log]"), None
    else:
        start, end = 1, total
        if total > _RF_DEFAULT_LINES:
            end = _RF_DEFAULT_LINES
    want = max(0, min(end - start + 1, _RF_MAX_LINES))
    rows = await asyncio.to_thread(lf.read_lines, str(p), start, want, _RF_LINE_CAP, idx)
    out, used, cut_lines = [], 0, 0
    for no, text, full in rows:
        if full > _RF_LINE_CAP:
            text += f" …[本行共 {full} 字节, 只显示前 {_RF_LINE_CAP}; 要全文用 execute_shell 跑脚本处理]"
            cut_lines += 1
        line = f"{no:4d}\t{text}"
        if out and used + len(line) + 1 > _RF_MAX_CHARS:
            break
        out.append(line)
        used += len(line) + 1
    shown_end = start + len(out) - 1 if out else start - 1
    if not lines_arg:
        try:
            td._read_cache_put(str(p), (mtime, total, idx.size))
        except Exception:
            pass
    body = "\n".join(out)
    whole = start == 1 and shown_end >= total
    if whole and not cut_lines:
        return body, None                      # 小文件整份读完: 输出与旧版一致
    remain = max(0, total - shown_end)
    head = f"[read_file 分段: {p.name} 共 {total} 行 / {lf.human_size(idx.size)}; 本次第 {start}-{shown_end} 行"
    if remain:
        head += f"; 后面还剩 {remain} 行未读"
    head += "]"
    tips = []
    if remain:
        nxt_end = min(total, shown_end + _RF_DEFAULT_LINES)
        tips.append(f"续读下一段: read_file(filepath='{p}', lines='{shown_end + 1}:{nxt_end}')")
        if shown_end < end:
            tips.append(f"(本次因输出上限 {_RF_MAX_CHARS} 字符/{_RF_MAX_LINES} 行停在第 {shown_end} 行)")
        tips.append("只看末尾: lines='-100:'")
        if ext in _RF_LOG_EXTS or idx.size > 5 * 1024 * 1024:
            tips.append("大日志别逐段通读: 先 search_code(query='ERROR|Traceback', path=<目录>) 或 "
                        "execute_shell 跑 grep -n / awk 统计定位, 再按行号读附近")
        if ext in _RF_DATA_EXTS:
            tips.append("数据文件别读进上下文: 用 execute_shell 跑 python/pandas (分块 chunksize) 统计、筛选, 只打印结果")
    tail = ("\n---\n[" + "; ".join(tips) + "]") if tips else ""
    return head + "\n" + body + tail, None


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


_TL_MAX_CHARS = 30000      # tail_log 单次输出预算: 超了丢最旧的行, 保留最新的
_TL_LINE_CAP = 2000


@register("tail_log")
async def h_tail_log(sid: str, args: dict) -> tuple[str, Any]:
    """[large-file 2026-10-21] 非 follow 模式走 lib/large_file 行索引: 不再读尾部 5MB 再 readlines,
    给出 共 N 行 / 显示区间 / 往前翻的 read_file 参数; 单行超 2000 字节截断, 总输出 ≤30000 字符。
    follow 模式只保留最近 1MB 缓冲, 日志狂刷也不涨内存。"""
    from lib import large_file as lf
    _tl_path = args.get("path", "").strip()
    try:
        _tl_lines = max(1, min(500, int(args.get("lines", 30))))
        _tl_follow = max(0, min(30, int(args.get("follow_seconds", 0))))
    except (TypeError, ValueError):
        return "ERROR: tail_log 的 lines / follow_seconds 必须是整数", None
    if not _tl_path:
        return "ERROR: tail_log 需要 path 参数", None
    _tp = Path(_tl_path)
    if not _tp.exists():
        return f"ERROR: 文件不存在: {_tl_path}", None
    if not _tp.is_file():
        return f"ERROR: 不是文件: {_tl_path}", None

    def _fit(lines: list[str], cap: bool = True) -> tuple[list[str], int]:
        """从最新往前装, 装不下的旧行丢掉; 返回 (保留行, 丢掉行数)。cap=False: 行已按字节截过。"""
        kept, used = [], 0
        for ln in reversed(lines):
            if cap and len(ln) > _TL_LINE_CAP:
                ln = ln[:_TL_LINE_CAP] + f" …[本行共 {len(ln)} 字符, 已截断]"
            if kept and used + len(ln) + 1 > _TL_MAX_CHARS:
                break
            kept.append(ln)
            used += len(ln) + 1
        kept.reverse()
        return kept, len(lines) - len(kept)

    try:
        if _tl_follow > 0:
            start_pos = max(0, _tp.stat().st_size - 8192)
            keep = 1024 * 1024

            async def _tail_follow():
                buf = ""
                with open(_tp, "r", errors="replace") as f:
                    f.seek(start_pos)
                    for _ in range(_tl_follow * 2):
                        chunk = f.read(keep)
                        while chunk:
                            buf = (buf + chunk)[-keep:]
                            chunk = f.read(keep)
                        await asyncio.sleep(0.5)
                return buf
            data = await asyncio.wait_for(_tail_follow(), timeout=_tl_follow + 5)
            kept, dropped = _fit(data.splitlines()[-_tl_lines:])
            note = f" (输出上限内丢了较早的 {dropped} 行)" if dropped else ""
            return f"[tail -f {_tl_follow}s, 最近 {len(kept)} 行 {_tl_path}{note}]\n" + "\n".join(kept), None
        rows, idx = await asyncio.to_thread(lf.tail_lines, str(_tp), _tl_lines, _TL_LINE_CAP)
        texts = []
        for _no, text, full in rows:
            if full > _TL_LINE_CAP:
                text += f" …[本行共 {full} 字节, 已截断]"
            texts.append(text)
        kept, dropped = _fit(texts, cap=False)
        if idx is None:                  # 超大文件且没建过索引: 倒读尾部, 不数总行数
            size = _tp.stat().st_size
            head = (f"[tail -n {_tl_lines} {_tl_path}] 文件 {lf.human_size(size)} (过大, 未统计总行数), "
                    f"显示最后 {len(kept)} 行")
            if dropped:
                head += f" (超出 {_TL_MAX_CHARS} 字符上限, 丢了较早的 {dropped} 行)"
            head += "; 定位关键字用 execute_shell grep -n / search_code, 再 read_file(lines='a:b') 读附近"
            return head + "\n" + "\n".join(kept), None
        first = rows[-len(kept)][0] if kept else idx.total_lines + 1
        head = (f"[tail -n {_tl_lines} {_tl_path}] 共 {idx.total_lines} 行 / {lf.human_size(idx.size)}, "
                f"显示第 {first}-{idx.total_lines} 行")
        if dropped:
            head += f" (超出 {_TL_MAX_CHARS} 字符上限, 丢了较早的 {dropped} 行)"
        if first > 1:
            prev_start = max(1, first - 200)
            head += (f"; 往前翻: read_file(filepath='{_tl_path}', lines='{prev_start}:{first - 1}'); "
                     "找关键字别逐段翻, 用 search_code 或 execute_shell grep -n")
        return head + "\n" + "\n".join(kept), None
    except Exception as _te:
        return f"ERROR tail_log: {_te}", None
