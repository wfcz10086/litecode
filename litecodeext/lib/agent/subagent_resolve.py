"""lib/agent/subagent_resolve.py — 子代理工作树合并冲突的自动解决 (保守、可回退)。

subagent_worktree 把子代理分支 merge --no-ff 回主工作区冲突时 (已 merge --abort, 主工作区干净), 走这里:
  1. 不碰主工作区: 在该子代理的工作树里把主分支当前 HEAD 反向合并进来 (conflictStyle=diff3, 带共同祖先段)。
  2. 确定性处理 (resolve_append_conflicts): 两边都只是在文件末尾 / 列表末尾追加 (BACKLOG、CHANGELOG、
     __all__、注册表) 的冲突块 → 两边都保留 (ours 在前, 即工作树这一侧在前)。
  3. 仍有冲突 → 派一个 coder 角色的解冲突子代理 (max_iter 20, 挂在该工作树上, 只许改冲突文件)。
  4. 校验: 冲突标记无残留 / 只改了冲突文件 / .py 能编译 / agent.worktree_verify_cmd (可选, 300s) 退出码 0。
     任一不过 → 工作树 merge --abort (不行就 reset --hard 回反向合并前), 结果退回"保留分支交回主代理"并写明原因。
  5. 通过 → 工作树里提交这次反向合并, 再 (持仓库锁) 对主工作区 merge --no-ff, 此时应无冲突。
开关: agent.worktree_auto_resolve (默认 true) 只管自动触发; agent_worktree(action=resolve) 手动触发不看开关。
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from . import subagent_worktree as _wt

RESOLVER_TYPE = "coder_merge"          # coder 角色的派生配置, 只把 max_iter 收到 20
RESOLVER_MAX_ITER = 20
RESOLVER_TIMEOUT = 900
VERIFY_TIMEOUT = 300
_DIFF_CHARS = 2500                     # 每个冲突文件每一侧 diff 摘要上限

_MARK_RE = re.compile(r"^(<{7}|\|{7}|>{7})(?: |$)", re.MULTILINE)
_CLOSERS = ("]", ")", "}")


# ═══════════════════════════ 配置 ═══════════════════════════

def _agent_cfg() -> dict:
    try:
        from ..config import CFG
        return CFG.get("agent") or {}
    except Exception:
        return {}


def auto_resolve_enabled() -> bool:
    return bool(_agent_cfg().get("worktree_auto_resolve", True))


def verify_cmd() -> str:
    return str(_agent_cfg().get("worktree_verify_cmd") or "").strip()


# ═══════════════════════════ 确定性解析 ═══════════════════════════

def has_conflict_markers(text: str) -> bool:
    return bool(_MARK_RE.search(text or ""))


def _block_resolvable(base: List[str], ours: List[str], theirs: List[str], tail: List[str]) -> Optional[List[str]]:
    """一个 diff3 冲突块: 两边都是"共同祖先 + 各自追加", 且块后只剩空白或紧跟列表/字典/调用的收尾括号。"""
    if base is None:                   # 非 diff3 格式, 不知道祖先, 不猜
        return None
    nb = len(base)
    if ours[:nb] != base or theirs[:nb] != base:
        return None
    after = next((ln for ln in tail if ln.strip()), None)
    if after is not None and not after.strip().startswith(_CLOSERS):
        return None
    return base + ours[nb:] + theirs[nb:]


def resolve_append_conflicts(text: str) -> Optional[str]:
    """含冲突标记的文本 → 解好的文本; 任一冲突块不是"两边都在末尾追加"则返回 None (整文件不处理)。

    只认 diff3 格式 (带 ||||||| 共同祖先段)。规则: 两边内容都以祖先段开头 (祖先可为空, 即纯插入),
    且冲突块之后只有空白 (文件末尾) 或下一个非空行是 ] ) } (列表/注册表末尾) → 祖先 + ours 追加 + theirs 追加。
    没有冲突标记的文本原样返回。
    """
    if text is None:
        return None
    lines = text.splitlines(keepends=True)
    out: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if not ln.startswith("<<<<<<<"):
            if _MARK_RE.match(ln):     # 孤立的 ||||||| / >>>>>>>: 格式不对, 不处理
                return None
            out.append(ln)
            i += 1
            continue
        ours, base, theirs = [], None, []
        cur = ours
        j = i + 1
        while j < n:
            s = lines[j]
            if s.startswith("|||||||") and base is None and cur is ours:
                base = []
                cur = base
            elif s.rstrip("\r\n") == "=======" and cur is not theirs:
                cur = theirs
            elif s.startswith(">>>>>>>") and cur is theirs:
                break
            elif s.startswith("<<<<<<<"):
                return None            # 嵌套冲突
            else:
                cur.append(s)
            j += 1
        if j >= n:
            return None                # 未闭合
        merged = _block_resolvable(base, ours, theirs, lines[j + 1:])
        if merged is None:
            return None
        # 各段末行可能缺换行 (原文件末尾无换行): 拼接时补上, 避免两段粘成一行; 块后还有内容时末行也补
        fixed = [s if s.endswith("\n") or (k == len(merged) - 1 and j + 1 >= n) else s + "\n"
                 for k, s in enumerate(merged)]
        out.extend(fixed)
        i = j + 1
    return "".join(out)


# ═══════════════════════════ git 辅助 ═══════════════════════════

def _git(args, cwd, timeout=_wt.GIT_TIMEOUT):
    return _wt._git(args, cwd, timeout)


def _unmerged(wt) -> List[str]:
    return _git(["diff", "--name-only", "--diff-filter=U"], wt).stdout.split()


def _rollback(wt: Path, pre_head: str) -> str:
    """撤销工作树里的反向合并: merge --abort, 不行再 reset --hard 回反向合并前。返回问题描述 (空=干净)。"""
    _git(["merge", "--abort"], wt)
    head = _git(["rev-parse", "HEAD"], wt).stdout.strip()
    if head != pre_head or _git(["status", "--porcelain", "--untracked-files=no"], wt).stdout.strip():
        _git(["reset", "-q", "--hard", pre_head], wt)
    head = _git(["rev-parse", "HEAD"], wt).stdout.strip()
    if head != pre_head:
        return f"工作树回退失败 (HEAD={head[:12]}, 应为 {pre_head[:12]})"
    return ""


def _side_summary(wt: Path, mb: str, tip: str, files: List[str]) -> Tuple[str, str]:
    log = _git(["log", "--no-merges", "--format=- %s", f"{mb}..{tip}"], wt).stdout.strip()
    parts = []
    for f in files:
        d = _git(["diff", f"{mb}..{tip}", "--", f], wt).stdout
        if len(d) > _DIFF_CHARS:
            d = d[:_DIFF_CHARS] + f"\n...[截断, 共 {len(d)} 字符]"
        parts.append(d.strip() or f"(本侧未改 {f})")
    return log or "(无提交说明)", "\n\n".join(parts)


# ═══════════════════════════ 解冲突子代理 ═══════════════════════════

def _ensure_resolver_config() -> None:
    from .subagent import _SUBAGENT_CONFIGS
    if RESOLVER_TYPE not in _SUBAGENT_CONFIGS:
        base = _SUBAGENT_CONFIGS["coder"]
        _SUBAGENT_CONFIGS[RESOLVER_TYPE] = {**base, "allowed": set(base["allowed"]),
                                            "max_iter": RESOLVER_MAX_ITER}


def _resolver_task(meta: dict, files: List[str], ours: Tuple[str, str], theirs: Tuple[str, str]) -> str:
    return (
        "[合并冲突解决] 你在一个 git 工作树里, 正处于 merge 中间状态。只解决下面这些文件里的冲突:\n"
        + "\n".join(f"- {f}" for f in files) + "\n\n"
        "要求:\n"
        "1. 逐个 read_file 冲突文件, 去掉所有冲突标记 (<<<<<<< / ||||||| / ======= / >>>>>>>), "
        "保留双方意图 (两边新增的都要留下, 同一处改法不同时合成一个同时满足双方目的的版本)。\n"
        "2. 只改上面列出的冲突文件, 不要动其他文件, 不要新建文件, 不要执行 git add/commit/merge/checkout/reset。\n"
        "3. 改完用 execute_shell 自查 (如 python -m py_compile <文件>、grep -n '<<<<<<<' <文件>), 然后简短说明怎么合的。\n\n"
        f"## 本侧 (ours, 子代理 {meta.get('id')} 的分支) 提交说明\n{ours[0]}\n"
        f"原任务: {(meta.get('task') or '')[:200]}\n\n"
        f"## 本侧 diff 摘要 (相对共同祖先)\n{ours[1]}\n\n"
        f"## 对侧 (theirs, 主分支当前 HEAD) 提交说明\n{theirs[0]}\n\n"
        f"## 对侧 diff 摘要 (相对共同祖先)\n{theirs[1]}\n"
    )


async def _run_resolver(meta: dict, task: str) -> str:
    """挂上工作树上下文跑解冲突子代理。独立 task 运行, 子代理内设置的 ContextVar (证据收集等) 不外泄。"""
    from . import subagent as _sa
    _ensure_resolver_config()
    repo, wtp = Path(meta["repo"]), Path(meta["path"])
    ctx = _wt.WorktreeCtx(wid=meta["id"], agent_type=RESOLVER_TYPE, repo_root=repo, wt_root=wtp,
                          workdir=Path(meta.get("workdir") or repo), branch=meta["branch"],
                          base_head=meta.get("base", ""))

    async def _go():
        _wt._ACTIVE.set(ctx)
        return await _sa._run_subagent(task, RESOLVER_TYPE, "")

    t = asyncio.create_task(_go())
    try:
        return await asyncio.wait_for(t, timeout=RESOLVER_TIMEOUT)
    except asyncio.TimeoutError:
        return f"[子代理超时] 解冲突子代理超过 {RESOLVER_TIMEOUT}s"


# ═══════════════════════════ 校验 ═══════════════════════════

def _changed_outside(wt: Path, allowed: List[str], untracked_before: set) -> List[str]:
    changed = set(_git(["diff", "--name-only"], wt).stdout.split())
    untracked = set(_git(["ls-files", "--others", "--exclude-standard"], wt).stdout.splitlines())
    changed |= (untracked - untracked_before)
    return sorted(f for f in changed if f not in set(allowed) and not _wt.is_junk(f))


def _verify(wt: Path, files: List[str], untracked_before: set) -> str:
    """返回失败原因 (空串 = 通过)。"""
    for f in files:
        p = wt / f
        if not p.exists():
            continue                   # 删改冲突解成删除, 由 git add -A 处理
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"读取 {f} 失败: {e}"
        if has_conflict_markers(txt):
            return f"{f} 仍有冲突标记"
        if f.endswith(".py"):
            try:
                compile(txt, f, "exec")      # 不落 __pycache__, 免得工作树多出未跟踪文件
            except SyntaxError as e:
                return f"{f} 语法错误 (py_compile 不过): 第 {e.lineno} 行 {e.msg}"
    extra = _changed_outside(wt, files, untracked_before)
    if extra:
        return f"解冲突时改动了冲突文件以外的文件: {', '.join(extra[:5])}"
    cmd = verify_cmd()
    if cmd:
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(wt), capture_output=True, text=True,
                               timeout=VERIFY_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"验证命令超时 ({VERIFY_TIMEOUT}s): {cmd}"
        if r.returncode != 0:
            tail = (r.stdout + r.stderr).strip()[-300:]
            return f"验证命令退出码 {r.returncode}: {cmd}" + (f"\n{tail}" if tail else "")
    return ""


# ═══════════════════════════ 主流程 ═══════════════════════════

def _start_reverse_merge(meta: dict) -> dict:
    """工作树里反向合并主分支 HEAD, 并做确定性处理。返回状态字典。"""
    wt, repo = Path(meta["path"]), meta["repo"]
    st = {"pre_head": _git(["rev-parse", "HEAD"], wt).stdout.strip(),
          "main_head": _git(["rev-parse", "HEAD"], repo).stdout.strip()}
    if not st["pre_head"] or not st["main_head"]:
        st["error"] = "取不到工作树或主分支 HEAD"
        return st
    if _git(["status", "--porcelain", "--untracked-files=no"], wt).stdout.strip():
        st["error"] = "工作树有未提交改动"
        return st
    st["untracked"] = set(_git(["ls-files", "--others", "--exclude-standard"], wt).stdout.splitlines())
    r = _git(_wt._ident(wt) + ["-c", "merge.conflictStyle=diff3", "merge", "--no-ff", "--no-commit",
                               st["main_head"]], wt, timeout=120)
    files = _unmerged(wt)
    if r.returncode != 0 and not files:
        st["error"] = f"反向合并失败: {(r.stderr or r.stdout).strip()[:300]}"
        return st
    st["files"] = files
    det, rest = [], []
    for f in files:
        p = wt / f
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            rest.append(f)             # 删改冲突 / 二进制 → 交给子代理
            continue
        fixed = resolve_append_conflicts(txt) if has_conflict_markers(txt) else None
        if fixed is not None and not has_conflict_markers(fixed):
            p.write_text(fixed, encoding="utf-8")
            det.append(f)
        else:
            rest.append(f)
    st["deterministic"], st["remaining"] = det, rest
    if files:
        mb = _git(["merge-base", st["pre_head"], st["main_head"]], wt).stdout.strip()
        st["ours"] = _side_summary(wt, mb, st["pre_head"], rest or files)
        st["theirs"] = _side_summary(wt, mb, st["main_head"], rest or files)
    return st


def _commit_reverse_merge(meta: dict, st: dict, desc: str) -> str:
    wt = Path(meta["path"])
    _git(["add", "-A", "--"] + st["files"], wt)
    left = _unmerged(wt)
    if left:
        return f"仍有未解决文件: {', '.join(left)}"
    r = _git(_wt._ident(wt) + ["commit", "-q", "--no-verify", "-m",
                               f"lc-agent {meta['id']}: 合并主分支 {st['main_head'][:12]} 并自动解冲突 ({desc})"], wt)
    if r.returncode != 0:
        return f"提交反向合并失败: {(r.stderr or r.stdout).strip()[:300]}"
    return ""


def _fail(meta: dict, reason: str, rollback_msg: str = "") -> dict:
    conflicts = meta.get("conflicts") or []
    note = f"合并冲突, 自动解决失败: {reason}; 已撤销工作树里的反向合并, 分支与工作树保留"
    if rollback_msg:
        note += f" ({rollback_msg})"
    meta.update(status="conflict", conflicts=conflicts, note=note,
                auto_resolve_error=reason)
    meta.pop("auto_resolved", None)
    return meta


async def auto_resolve(meta: dict) -> dict:
    """对已 merge --abort 的冲突分支做自动解冲突 + 合并。返回更新后的 meta (调用方负责 _save_meta)。

    调用方不持仓库锁 (解冲突子代理可能跑很久); 最后对主工作区的合并在这里自己拿锁。
    """
    wt = Path(meta.get("path") or "")
    if not wt.is_dir():
        return _fail(meta, "工作树目录不存在")
    st = await asyncio.to_thread(_start_reverse_merge, meta)
    if st.get("error"):
        msg = await asyncio.to_thread(_rollback, wt, st["pre_head"]) if st.get("pre_head") else ""
        return _fail(meta, st["error"], msg)
    files = st["files"]
    methods = {f: "确定性" for f in st["deterministic"]}
    if st["remaining"]:
        task = _resolver_task(meta, st["remaining"], st["ours"], st["theirs"])
        try:
            out = await _run_resolver(meta, task)
        except Exception as e:
            out = f"[子代理异常] {e}"
        if isinstance(out, str) and out.startswith(_wt._ABORT_PREFIXES):
            msg = await asyncio.to_thread(_rollback, wt, st["pre_head"])
            return _fail(meta, f"解冲突子代理未正常结束: {out[:200]}", msg)
        methods.update({f: "子代理" for f in st["remaining"]})
    reason = await asyncio.to_thread(_verify, wt, files, st["untracked"])
    if not reason:
        desc = ", ".join(f"{f}({m})" for f, m in methods.items()) or "无文本冲突"
        reason = await asyncio.to_thread(_commit_reverse_merge, meta, st, desc)
    if reason:
        msg = await asyncio.to_thread(_rollback, wt, st["pre_head"])
        return _fail(meta, reason, msg)
    meta["commit"] = _git(["rev-parse", "HEAD"], wt).stdout.strip()
    stats = await asyncio.to_thread(_wt._branch_stats, meta["repo"], meta["base"], meta["branch"])
    meta.update(stats)
    async with _wt._lock_for(meta["repo"]):
        meta = await asyncio.to_thread(_wt._merge_sync, meta)
    if meta.get("status") == "merged":
        desc = ", ".join(f"{f} ({m})" for f, m in methods.items())
        meta.update(auto_resolved={"files": files, "methods": methods},
                    note=f"冲突已自动解决: {desc}")
        meta.pop("conflicts", None)
        meta.pop("auto_resolve_error", None)
    elif meta.get("status") == "conflict":
        meta["note"] = ("冲突已在工作树里自动解决并提交, 但合并回主工作区时再次冲突 "
                        "(主分支在解冲突期间又有新提交), 已 merge --abort, 可再 agent_worktree resolve")
    return meta

