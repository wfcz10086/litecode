"""lib/agent/subagent_worktree.py — 编码类子代理的独立 git 工作树隔离。

问题: 所有子代理共用一个工作目录, 两个 coder 并行改同一仓库会互相覆盖。
做法: spawn_agent 派发时 (plan_isolation) 判断是否需要隔离; 需要时把 run_subagent_fn
包一层 (IsolationPlan.wrap), 每个写类子代理:
  1. 在 <WORKSPACE>/.agent_worktrees/<id> 建 `git worktree add -b lc-agent/<id> HEAD`
  2. 运行期间经 ContextVar 挂上 WorktreeCtx, subagent._run_subagent 执行工具前调
     rewrite_tool_args 把落在原仓库根下的路径映射到工作树, execute_shell 前置 cd 到工作树
  3. 结束后在工作树 add+commit, 再 (按仓库串行) `git merge --no-ff` 回主工作区;
     成功则删工作树与分支, 冲突则 abort 并保留, 交主代理用 agent_worktree 工具处理。
元数据落盘 <WORKSPACE>/.agent_worktrees/_meta/<id>.json, 供 agent_worktree list/merge/diff/discard。

路径映射局限: execute_shell 命令里只做绝对路径的字符串前缀替换, 拼接出来的路径
(如 "$ROOT/x"、cd .. 后再访问、脚本内部写死的路径) 映射不到; 子代理提示词里已告知工作目录。
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

WORKTREE_DIRNAME = ".agent_worktrees"
BRANCH_PREFIX = "lc-agent/"
STALE_SECONDS = 24 * 3600
GIT_TIMEOUT = 60
_WRITE_TOOLS = {"write_file", "patch_file", "apply_blocks", "edit_symbol"}
_EXTRA_WRITE_ROLES = {"tester", "shell"}       # 只有 execute_shell 但会在仓库里跑/产出文件
_NON_AGENT_TYPES = {"tool", "dag"}             # DAG 原生工具步 / 子 DAG 步, 不跑子代理
_DONE_STATUSES = {"merged", "discarded", "no_changes"}
_ABORT_PREFIXES = ("[子代理超时]", "[子代理已中断]", "[子代理异常]", "[子代理已取消")

_ACTIVE: contextvars.ContextVar[Optional["WorktreeCtx"]] = contextvars.ContextVar(
    "subagent_worktree", default=None)
_LOCKS: Dict[tuple, asyncio.Lock] = {}

# 测试可覆盖: None = 用 lib.config.WORKSPACE
WORKSPACE_OVERRIDE: Optional[Path] = None


def _workspace() -> Path:
    if WORKSPACE_OVERRIDE is not None:
        return Path(WORKSPACE_OVERRIDE)
    from ..config import WORKSPACE
    return Path(WORKSPACE)


def _base_dir() -> Path:
    return _workspace() / WORKTREE_DIRNAME


def _meta_dir() -> Path:
    return _base_dir() / "_meta"


# ═══════════════════════════ git 调用 ═══════════════════════════

def _git(args: List[str], cwd, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """所有 git 调用的唯一出口: 带超时 + safe.directory=* (容器里属主可能不同) + 不弹交互。"""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    cmd = ["git", "-c", "safe.directory=*", "-c", "core.quotepath=false"] + list(args)
    try:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"git {' '.join(args[:2])} 超时 {timeout}s")
    except Exception as e:  # git 不存在 / cwd 不存在
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def _git_ok(args, cwd, timeout=GIT_TIMEOUT) -> str:
    r = _git(args, cwd, timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"git {args[0]} rc={r.returncode}").strip()[:400])
    return r.stdout.strip()


def _ident(repo) -> List[str]:
    """仓库没配 user.name/email 时补默认身份, 否则 commit/merge 会失败。"""
    out = []
    if not _git(["config", "user.name"], repo).stdout.strip():
        out += ["-c", "user.name=LiteCode Agent"]
    if not _git(["config", "user.email"], repo).stdout.strip():
        out += ["-c", "user.email=agent@litecode.local"]
    return out


def repo_root_of(path) -> Optional[Path]:
    r = _git(["rev-parse", "--show-toplevel"], path)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip())


def _dirty_files(repo) -> List[str]:
    """已跟踪文件的未提交改动 (未跟踪文件不算, 它们不随 HEAD 也一般不挡合并)。"""
    r = _git(["status", "--porcelain", "--untracked-files=no"], repo)
    return [ln[3:] for ln in r.stdout.splitlines() if ln.strip()]


def _lock_for(repo) -> asyncio.Lock:
    """同一仓库的 worktree add / merge / remove 串行化。锁绑定事件循环, 故按 (仓库, loop) 取。"""
    key = (str(repo), id(asyncio.get_running_loop()))
    lk = _LOCKS.get(key)
    if lk is None:
        lk = _LOCKS[key] = asyncio.Lock()
    return lk


# ═══════════════════════════ 元数据 ═══════════════════════════

def _save_meta(meta: dict) -> None:
    meta["updated"] = time.time()
    d = _meta_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{meta['id']}.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    os.replace(tmp, d / f"{meta['id']}.json")


def load_meta(wid: str) -> Optional[dict]:
    p = _meta_dir() / f"{wid}.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def all_meta() -> List[dict]:
    out = []
    d = _meta_dir()
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except Exception:
                continue
    return out


# ═══════════════════════════ 路径映射 ═══════════════════════════

def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip("/") + "/")


@dataclass
class WorktreeCtx:
    wid: str
    agent_type: str
    repo_root: Path      # 原仓库 (主工作区) 根
    wt_root: Path        # 工作树根
    workdir: Path        # 主代理给的 workdir (原仓库内)
    branch: str
    base_head: str

    @property
    def wt_workdir(self) -> Path:
        return self.wt_root / os.path.relpath(self.workdir, self.repo_root)

    def _roots(self) -> List[str]:
        rs = {os.path.normpath(str(self.repo_root)), os.path.realpath(str(self.repo_root))}
        return sorted(rs, key=len, reverse=True)

    def map_path(self, raw: str, empty_to_workdir: bool = False) -> str:
        """原仓库根下的绝对路径 → 工作树对应路径; 相对路径以工作树中的 workdir 为基准。"""
        if not raw:
            return str(self.wt_workdir) if empty_to_workdir else raw
        p = os.path.expanduser(str(raw).strip())
        if not os.path.isabs(p):
            return os.path.normpath(os.path.join(str(self.wt_workdir), p))
        np_ = os.path.normpath(p)
        base = os.path.normpath(str(_base_dir()))
        if _under(np_, os.path.normpath(str(self.wt_root))) or _under(np_, base):
            return np_
        for r in self._roots():
            if _under(np_, r):
                rel = os.path.relpath(np_, r)
                return os.path.normpath(os.path.join(str(self.wt_root), rel))
        return np_

    def map_command(self, cmd: str) -> str:
        """execute_shell: 原仓库绝对路径做前缀替换, 并前置 cd 到工作树中的 workdir。"""
        wt = str(self.wt_root)
        skip = WORKTREE_DIRNAME
        for r in self._roots():
            pat = re.compile(r"(?<![\w./-])" + re.escape(r.rstrip("/")) +
                             r"(?=$|[/\s'\";:)|&>]|`)(?!/" + re.escape(skip) + r"(?:/|$))")
            cmd = pat.sub(lambda _m: wt, cmd)
        return f"cd {shlex.quote(str(self.wt_workdir))} && {cmd}"


_FILEPATH_TOOLS = {"read_file", "write_file", "patch_file", "edit_symbol", "locate_symbol"}
_DIR_TOOLS = {"get_tree", "find_files", "search_code"}
_PATH_TOOLS = {"tail_log", "vision_ocr"}
_BLOCK_PATH_RE = re.compile(r"^([^\n]+)(\n<{7}\s*SEARCH\n)", re.MULTILINE)


def current() -> Optional[WorktreeCtx]:
    return _ACTIVE.get()


def rewrite_tool_args(fn_name: str, fn_args: dict) -> dict:
    """子代理级路径改写钩子: 只在当前子代理挂了工作树时生效, 主代理/其他子代理不受影响。"""
    ctx = _ACTIVE.get()
    if ctx is None or not isinstance(fn_args, dict):
        return fn_args
    a = dict(fn_args)
    if fn_name in _FILEPATH_TOOLS and a.get("filepath"):
        a["filepath"] = ctx.map_path(a["filepath"])
    elif fn_name in _DIR_TOOLS:
        a["path"] = ctx.map_path(a.get("path", ""), empty_to_workdir=True)
    elif fn_name in _PATH_TOOLS and a.get("path"):
        a["path"] = ctx.map_path(a["path"])
    elif fn_name == "apply_blocks" and a.get("blocks"):
        a["blocks"] = _BLOCK_PATH_RE.sub(
            lambda m: ctx.map_path(m.group(1).strip()) + m.group(2), a["blocks"])
    elif fn_name == "execute_shell" and a.get("command"):
        a["command"] = ctx.map_command(a["command"])
    return a


def prompt_section(ctx: WorktreeCtx) -> str:
    return (
        "\n\n## 工作目录 (独立 git 工作树, 与其他并行子代理隔离)\n"
        f"- 你的工作目录: {ctx.wt_workdir}\n"
        f"- 它对应原项目目录 {ctx.workdir}; 写原项目绝对路径会被自动映射到工作树, "
        "相对路径以上面的工作目录为基准, execute_shell 默认在该目录执行。\n"
        "- 不要 cd 到工作树之外改原项目, 也不要自己 git commit/merge/checkout —— "
        "结束后框架会自动提交并合并回主工作区。"
    )


# ═══════════════════════════ 角色 / 并发判定 ═══════════════════════════

def is_write_role(agent_type: str) -> bool:
    if agent_type in _NON_AGENT_TYPES:
        return False
    from .subagent import _SUBAGENT_CONFIGS
    cfg = _SUBAGENT_CONFIGS.get(agent_type)
    if cfg is None:          # 未知类型在 _run_subagent 里回落 coder
        return True
    return bool(cfg["allowed"] & _WRITE_TOOLS) or agent_type in _EXTRA_WRITE_ROLES


def _concurrent_writers(args: dict) -> int:
    """本次派发里会同时运行的写类子代理数 (≥2 才需要 auto 隔离)。"""
    par = args.get("parallel_tasks") or args.get("competitive_tasks")
    if isinstance(par, list) and not args.get("dag_tasks"):
        return sum(1 for t in par if isinstance(t, dict) and is_write_role(t.get("agent_type", "coder")))
    dag = args.get("dag_tasks")
    if isinstance(dag, list):
        ids, deps, writers = [], {}, []
        for i, t in enumerate(x for x in dag if isinstance(x, dict)):
            sid = t.get("step_id") or f"step_{i+1}"
            ids.append(sid)
            deps[sid] = list(t.get("depends_on") or [])
            if is_write_role(t.get("agent_type", "coder")):
                writers.append(sid)

        def _anc(s, seen=None):
            seen = set() if seen is None else seen
            for d in deps.get(s, []):
                if d not in seen:
                    seen.add(d)
                    _anc(d, seen)
            return seen
        anc = {s: _anc(s) for s in writers}
        best = 0
        for a in writers:  # 与 a 无祖先关系的写步数 (粗估最大并发)
            n = 1 + sum(1 for b in writers if b != a and b not in anc[a] and a not in anc[b])
            best = max(best, n)
        return best
    return 0      # pipeline / batch_items / 单代理: 串行


# ═══════════════════════════ 派发级计划 ═══════════════════════════

@dataclass
class IsolationPlan:
    enabled: bool = False
    mode: str = "auto"
    workdir: Optional[Path] = None
    repo_root: Optional[Path] = None
    notes: List[str] = field(default_factory=list)
    reports: List[dict] = field(default_factory=list)

    def wrap(self, run_fn: Callable) -> Callable:
        if not self.enabled:
            return run_fn
        plan = self

        async def _isolated(task, agent_type="coder", context="", sse_emit=None, *a, **kw):
            if not is_write_role(agent_type):
                return await run_fn(task, agent_type, context, sse_emit, *a, **kw)
            try:
                ctx = await create_worktree(plan.repo_root, plan.workdir, agent_type, task)
            except Exception as e:
                plan.notes.append(f"{agent_type} 创建工作树失败, 未隔离直接在原目录运行: {e}")
                return await run_fn(task, agent_type, context, sse_emit, *a, **kw)
            token = _ACTIVE.set(ctx)
            try:
                out = await run_fn(task, agent_type, context, sse_emit, *a, **kw)
            except BaseException:
                _ACTIVE.reset(token)
                try:                              # 超时/取消: 只提交并保留, 不合并
                    _commit_sync(ctx, task)
                except Exception:
                    pass
                rep = load_meta(ctx.wid) or {"id": ctx.wid}
                rep.update(status="committed" if rep.get("commit") else "running",
                           note="子代理被取消/超时/异常, 现有改动已提交但未合并, 工作树保留")
                _save_meta(rep)
                plan.reports.append(rep)
                _note_evidence(rep)
                raise
            _ACTIVE.reset(token)
            aborted = isinstance(out, str) and out.startswith(_ABORT_PREFIXES)
            rep = await finalize_worktree(ctx, task, merge=not aborted)
            plan.reports.append(rep)
            _note_evidence(rep)
            return f"{out}\n\n{format_report(rep, short=True)}"
        return _isolated

    def summary(self) -> str:
        if not self.notes and not self.reports:
            return ""
        lines = ["", "--- 子代理工作树隔离 ---"]
        lines += [f"注意: {n}" for n in self.notes]
        lines += [format_report(r) for r in self.reports]
        if any(r.get("status") not in _DONE_STATUSES for r in self.reports):
            lines.append("未合并的工作树已保留: 用 agent_worktree(action=diff/merge/discard, id=...) 处理, "
                         "或手动解决冲突后 agent_worktree merge。")
        return "\n".join(lines)


def _note_evidence(rep: dict) -> None:
    """把提交/合并结果交给外层证据包装器 (subagent_evidence), 进「证据」小节。"""
    try:
        from .subagent_evidence import note_worktree
        note_worktree(rep)
    except Exception:
        pass


def plan_isolation(args: dict, single: bool = False) -> IsolationPlan:
    """按 spawn_agent 参数决定本次派发是否用工作树隔离。"""
    mode = str(args.get("isolation") or "auto").strip().lower()
    plan = IsolationPlan(mode=mode)
    if mode not in ("auto", "worktree", "none"):
        plan.notes.append(f"未知 isolation={mode!r}, 按 auto 处理")
        mode = plan.mode = "auto"
    if mode == "none":
        return plan
    if mode == "auto":
        if single or _concurrent_writers(args) < 2:
            return plan
    raw = str(args.get("workdir") or "").strip()
    wd = Path(os.path.expanduser(raw)) if raw else _workspace()
    if not wd.is_absolute():
        wd = _workspace() / wd
    if not wd.is_dir():
        plan.notes.append(f"workdir {wd} 不存在, 子代理未隔离 (共用工作目录, 并行写同一文件可能互相覆盖)")
        return plan
    root = repo_root_of(wd)
    if root is None or _git(["rev-parse", "--verify", "HEAD"], root).returncode != 0:
        plan.notes.append(f"workdir {wd} 不是 git 仓库 (或没有任何提交), 子代理未隔离 "
                          "(共用工作目录, 并行写同一文件可能互相覆盖)")
        return plan
    plan.enabled, plan.workdir, plan.repo_root = True, wd.resolve(), root
    dirty = _dirty_files(root)
    if dirty:
        plan.notes.append(
            f"主工作区 {root} 有 {len(dirty)} 个未提交改动 ({', '.join(dirty[:5])}): "
            "工作树基于 HEAD, 不含这些改动; 结束后不会自动合并, 请先提交/暂存后用 agent_worktree merge")
    return plan


# ═══════════════════════════ 创建 / 收尾 ═══════════════════════════

def _ensure_excluded(repo: Path) -> None:
    """工作树目录落在仓库内时, 写进 info/exclude, 免得主工作区 git status 里冒出来。"""
    base = _base_dir()
    try:
        rel = os.path.relpath(base.resolve(), repo.resolve())
    except Exception:
        return
    if rel.startswith(".."):
        return
    common = _git(["rev-parse", "--git-common-dir"], repo).stdout.strip()
    if not common:
        return
    ex = (repo / common if not os.path.isabs(common) else Path(common)) / "info" / "exclude"
    line = f"/{rel}/"
    try:
        old = ex.read_text() if ex.exists() else ""
        if line not in old.splitlines():
            ex.parent.mkdir(parents=True, exist_ok=True)
            ex.write_text(old + ("" if old.endswith("\n") or not old else "\n") + line + "\n")
    except Exception:
        pass


async def create_worktree(repo: Path, workdir: Path, agent_type: str, task: str = "") -> WorktreeCtx:
    wid = f"{agent_type}-{uuid.uuid4().hex[:8]}"
    branch = BRANCH_PREFIX + wid
    wt = _base_dir() / wid
    async with _lock_for(repo):
        def _do():
            _base_dir().mkdir(parents=True, exist_ok=True)
            _ensure_excluded(repo)
            head = _git_ok(["rev-parse", "HEAD"], repo)
            _git_ok(["worktree", "add", str(wt), "-b", branch, "HEAD"], repo, timeout=120)
            return head
        head = await asyncio.to_thread(_do)
    ctx = WorktreeCtx(wid=wid, agent_type=agent_type, repo_root=Path(repo), wt_root=wt,
                      workdir=Path(workdir), branch=branch, base_head=head)
    _save_meta({"id": wid, "agent_type": agent_type, "repo": str(repo), "path": str(wt),
                "workdir": str(workdir), "branch": branch, "base": head, "status": "running",
                "task": task[:200], "created": time.time()})
    return ctx


def _commit_sync(ctx: WorktreeCtx, task: str) -> Optional[str]:
    """在工作树里 add -A + commit; 无改动返回 None。"""
    wt = ctx.wt_root
    if not wt.is_dir():
        return None
    if not _git(["status", "--porcelain"], wt).stdout.strip():
        return None
    _git_ok(["add", "-A"], wt)
    msg = f"lc-agent {ctx.wid}: {ctx.agent_type} {' '.join(task.split())[:60]}"
    _git_ok(_ident(wt) + ["commit", "-q", "--no-verify", "-m", msg], wt)
    meta = load_meta(ctx.wid) or {}
    meta.update(status="committed", commit=_git_ok(["rev-parse", "HEAD"], wt))
    _save_meta(meta)
    return meta["commit"]


def _branch_stats(repo, base: str, branch: str) -> dict:
    files = _git(["diff", "--name-only", f"{base}..{branch}"], repo).stdout.split()
    stat = _git(["diff", "--shortstat", f"{base}..{branch}"], repo).stdout.strip()
    return {"files": files, "diffstat": stat}


def _remove_worktree(repo, path, branch) -> None:
    _git(["worktree", "remove", "--force", str(path)], repo, timeout=120)
    if Path(path).exists():
        import shutil
        shutil.rmtree(path, ignore_errors=True)
    _git(["worktree", "prune"], repo)
    _git(["branch", "-D", branch], repo)


def _merge_sync(meta: dict) -> dict:
    """把分支合并回主工作区 (调用方持仓库锁)。更新并返回 meta。"""
    repo = meta["repo"]
    dirty = _dirty_files(repo)
    if dirty:
        meta.update(status="pending_dirty",
                    note=f"主工作区有未提交改动 ({', '.join(dirty[:5])}), 未自动合并")
        return meta
    r = _git(_ident(repo) + ["merge", "--no-ff", "--no-edit", "-m",
                             f"合并子代理 {meta['id']} ({meta.get('agent_type', '')})", meta["branch"]],
             repo, timeout=120)
    if r.returncode == 0:
        meta.update(status="merged", merge_commit=_git(["rev-parse", "HEAD"], repo).stdout.strip())
        _remove_worktree(repo, meta["path"], meta["branch"])
        return meta
    conflicts = _git(["diff", "--name-only", "--diff-filter=U"], repo).stdout.split()
    _git(["merge", "--abort"], repo)
    meta.update(status="conflict", conflicts=conflicts,
                note=(f"合并冲突, 已 merge --abort, 分支与工作树保留" if conflicts else
                      f"合并失败: {(r.stderr or r.stdout).strip()[:300]}"))
    return meta


async def finalize_worktree(ctx: WorktreeCtx, task: str = "", merge: bool = True) -> dict:
    """子代理结束: 提交 → 统计 → 自动合并 (无改动则直接清理; merge=False 只提交保留)。"""
    try:
        commit = await asyncio.to_thread(_commit_sync, ctx, task)
    except Exception as e:
        meta = load_meta(ctx.wid) or {"id": ctx.wid}
        meta.update(status="commit_failed", note=f"工作树提交失败: {e}")
        _save_meta(meta)
        return meta
    meta = load_meta(ctx.wid) or {}
    stats = await asyncio.to_thread(_branch_stats, ctx.repo_root, ctx.base_head, ctx.branch)
    meta.update(stats, commit=commit or meta.get("commit"))
    if not stats["files"]:
        async with _lock_for(ctx.repo_root):
            await asyncio.to_thread(_remove_worktree, ctx.repo_root, ctx.wt_root, ctx.branch)
        meta.update(status="no_changes")
        _save_meta(meta)
        return meta
    if not merge:
        meta.update(status="committed", note="子代理超时/中断/异常, 改动已提交但未自动合并, 请先 diff 核对")
        _save_meta(meta)
        return meta
    async with _lock_for(ctx.repo_root):
        meta = await asyncio.to_thread(_merge_sync, meta)
    _save_meta(meta)
    return meta


_STATUS_CN = {"merged": "已合并", "conflict": "合并冲突", "pending_dirty": "未合并(主工作区有改动)",
              "no_changes": "无改动", "committed": "已提交未合并", "running": "运行中",
              "discarded": "已丢弃", "commit_failed": "提交失败"}


def format_report(m: dict, short: bool = False) -> str:
    st = m.get("status", "?")
    head = f"[工作树 {m.get('id')}] {_STATUS_CN.get(st, st)}"
    if short:
        return head + (f" 冲突文件: {', '.join(m.get('conflicts') or [])}" if st == "conflict" else "")
    parts = [head, f"  分支: {m.get('branch')}  提交: {(m.get('commit') or '-')[:12]}"]
    if m.get("files"):
        parts.append(f"  改动文件: {', '.join(m['files'][:20])}" + (" ..." if len(m['files']) > 20 else ""))
    if m.get("diffstat"):
        parts.append(f"  diff 统计: {m['diffstat']}")
    if m.get("merge_commit"):
        parts.append(f"  合并提交: {m['merge_commit'][:12]} (工作树与分支已删除)")
    if m.get("conflicts"):
        parts.append(f"  冲突文件: {', '.join(m['conflicts'])}")
    if st not in _DONE_STATUSES and m.get("path"):
        parts.append(f"  工作树保留在: {m['path']}")
    if m.get("note"):
        parts.append(f"  说明: {m['note']}")
    return "\n".join(parts)


# ═══════════════════════════ 清理 + agent_worktree 工具 ═══════════════════════════

def cleanup_stale(max_age: float = STALE_SECONDS) -> int:
    """清理超过 max_age 且已合并/已丢弃/无改动的残留 (目录、分支、元数据) 并 git worktree prune。"""
    n, repos = 0, set()
    now = time.time()
    for m in all_meta():
        if m.get("repo"):
            repos.add(m["repo"])
        if m.get("status") in _DONE_STATUSES and now - m.get("updated", now) > max_age:
            if m.get("repo") and Path(m["repo"]).is_dir():
                if Path(m.get("path", "")).exists() or m.get("status") != "merged":
                    _remove_worktree(m["repo"], m.get("path", ""), m.get("branch", ""))
            (_meta_dir() / f"{m['id']}.json").unlink(missing_ok=True)
            n += 1
    for r in repos:
        if Path(r).is_dir():
            _git(["worktree", "prune"], r)
    return n


async def agent_worktree_tool(args: dict) -> str:
    action = str(args.get("action") or "list").strip().lower()
    wid = str(args.get("id") or "").strip()
    if action == "list":
        cleaned = await asyncio.to_thread(cleanup_stale)
        ms = [m for m in all_meta() if m.get("status") not in _DONE_STATUSES]
        head = f"清理过期残留 {cleaned} 个。" if cleaned else ""
        if not ms:
            return head + "没有待处理的子代理工作树。"
        return head + f"待处理工作树 {len(ms)} 个:\n" + "\n".join(format_report(m) for m in ms)
    if action not in ("merge", "discard", "diff"):
        return f"ERROR: 未知 action={action!r}, 可选 list / merge / discard / diff"
    meta = load_meta(wid) if wid else None
    if not meta:
        return f"ERROR: 找不到工作树 id={wid!r} (先 agent_worktree list 看)"
    repo = meta["repo"]
    if action == "diff":
        def _d():
            out = _git(["diff", "--stat", f"{meta['base']}..{meta['branch']}"], repo).stdout
            out += _git(["diff", f"{meta['base']}..{meta['branch']}"], repo).stdout
            if Path(meta["path"]).is_dir():
                unc = _git(["diff", "HEAD"], meta["path"]).stdout
                if unc:
                    out += "\n[工作树内未提交改动]\n" + unc
            return out
        out = await asyncio.to_thread(_d)
        if len(out) > 8000:
            out = out[:8000] + f"\n...[截断, 共 {len(out)} 字符]"
        return out or "(无差异)"
    if meta.get("status") in _DONE_STATUSES:
        return f"工作树 {wid} 状态为 {meta['status']}, 无需处理"
    async with _lock_for(repo):
        if action == "discard":
            await asyncio.to_thread(_remove_worktree, repo, meta["path"], meta["branch"])
            meta.update(status="discarded", note="已丢弃: 工作树与分支已删除")
        else:
            if Path(meta["path"]).is_dir():
                ctx = WorktreeCtx(wid, meta.get("agent_type", ""), Path(repo), Path(meta["path"]),
                                  Path(meta.get("workdir", repo)), meta["branch"], meta["base"])
                await asyncio.to_thread(_commit_sync, ctx, "agent_worktree merge 前补提交")
                meta = load_meta(wid) or meta
            meta = await asyncio.to_thread(_merge_sync, meta)
    _save_meta(meta)
    return format_report(meta)
