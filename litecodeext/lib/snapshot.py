"""框架级工作区快照 — 给 agent 改的项目做 task 边界的可回滚快照。

动机 (2026-09-03): 中大型任务里 agent 会对一个项目目录做多轮改写
(今天 v2 那次 handleCast 三段式重写就是), 一旦某步改崩, 现在只能靠它自己
apply_blocks 往回改, 没有干净的回滚点。这个模块在每个 task 边界打一个快照,
需要时能 reset 回上一个绿点。

设计取舍:
- **旁路 git 仓**: git 目录放在项目**外面** (workspace/.snapshots/<hash>.git),
  work-tree 指向项目。这样不在项目里塞 .git, 不跟 agent 自己的 git init 打架,
  也不污染它的提交历史。
- **只快照, 不自动回滚**: 每个 task 结束自动 commit 一个快照 (复用 on_task_end
  钩子)。回滚是显式动作 (工具 / 人), 因为"什么算改崩了"没有可靠的自动信号 ——
  这正是前几天讨论的: 没有外部 verifier 时, 自动判断"该回滚"本身不可靠。
- **快照对象自推断**: 从本回合写入的文件路径取共同父目录, 不依赖 _pm_state
  (后者只在相对路径且落 workspace 内时才有值, 绝对路径项目推不出来)。
- **失败绝不影响主流程**: 所有 git 调用包在 try 里, 失败 log.warning 不抛。
  快照是安全网, 网破了不该把人也拖下水。
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("openclaw")

_SNAP_DIRNAME = ".snapshots"          # 放在 workspace 下
_MAX_SNAPSHOTS_PER_PROJECT = 30       # 每项目保留的快照上限 (超了删最老的 tag)


def _run(args: list[str], cwd: Optional[str] = None, timeout: int = 60) -> tuple[int, str]:
    """跑一条 git, 返回 (rc, 合并输出)。绝不抛。"""
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def infer_project_root(written_paths: list[str], fallback: Optional[str] = None) -> Optional[str]:
    """从本回合写入的文件路径推断项目根 = 它们的共同父目录。

    只认真实存在的目录; 过滤掉 /tmp 直挂的散文件 (共同父是 /tmp 时不算项目)。
    """
    dirs = []
    for p in written_paths:
        try:
            d = os.path.dirname(os.path.abspath(p))
            if d and os.path.isdir(d):
                dirs.append(d)
        except Exception:
            continue
    if not dirs:
        return fallback
    common = os.path.commonpath(dirs)
    # 共同父落到系统级目录就不当项目 (防止给 /tmp、/、/home 打快照)
    if common in ("/", "/tmp", "/home", "/root", "/var", "/opt", os.path.expanduser("~")):
        return fallback
    return common


def _gitdir_for(workspace: Path, project_root: str) -> Path:
    """每个项目一个旁路 git 目录, 用项目路径 hash 命名, 避免冲突。"""
    h = hashlib.md5(project_root.encode()).hexdigest()[:12]
    return Path(workspace) / _SNAP_DIRNAME / f"{h}.git"


def _git_env(gitdir: Path, worktree: str) -> list[str]:
    """构造 git 前缀: 旁路 git-dir + work-tree 指向项目。"""
    return ["git", f"--git-dir={gitdir}", f"--work-tree={worktree}"]


def snapshot(workspace: Path, project_root: str, label: str) -> Optional[str]:
    """给 project_root 打一个快照, 返回短 commit hash (失败返回 None, 不抛)。

    label 进 commit message, 用于人/工具识别是哪个 task 的边界。
    """
    try:
        if not project_root or not os.path.isdir(project_root):
            return None
        gitdir = _gitdir_for(workspace, project_root)
        if not gitdir.exists():
            gitdir.parent.mkdir(parents=True, exist_ok=True)
            rc, out = _run(["git", "init", "--bare", str(gitdir)])
            if rc != 0:
                log.warning(f"[snapshot] git init 失败 {gitdir}: {out[:120]}")
                return None
            # 旁路仓不需要身份配置以外的东西; 身份用 -c 传, 不写全局
        g = _git_env(gitdir, project_root)
        ident = ["-c", "user.name=litecode-snapshot", "-c", "user.email=snap@litecode.local"]
        # add 全部 (含未跟踪), 但尊重项目自己的 .gitignore
        rc, _ = _run(g + ["add", "-A"], cwd=project_root)
        if rc != 0:
            return None
        # 无改动时 commit 会失败 —— 允许空提交, 保证 task 边界总有锚点
        rc, out = _run(g + ident + ["commit", "--allow-empty", "-q",
                                    "-m", f"[snapshot] {label} @ {time.strftime('%Y-%m-%d %H:%M:%S')}"],
                       cwd=project_root)
        if rc != 0:
            log.warning(f"[snapshot] commit 失败: {out[:120]}")
            return None
        rc, sha = _run(g + ["rev-parse", "--short", "HEAD"], cwd=project_root)
        _prune(workspace, project_root)
        if rc == 0:
            log.info(f"  \033[2m[SNAPSHOT]\033[0m {os.path.basename(project_root)} @ {sha} ({label})")
            return sha
    except Exception as exc:
        log.warning(f"[snapshot] 异常 (已忽略): {exc}")
    return None


def list_snapshots(workspace: Path, project_root: str, limit: int = 20) -> list[dict]:
    """列出某项目的快照 (最新在前)。"""
    gitdir = _gitdir_for(workspace, project_root)
    if not gitdir.exists():
        return []
    g = _git_env(gitdir, project_root)
    rc, out = _run(g + ["log", f"-{limit}", "--format=%h\t%ci\t%s"], cwd=project_root)
    if rc != 0 or not out:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append({"hash": parts[0], "time": parts[1][:19], "label": parts[2]})
    return rows


def restore(workspace: Path, project_root: str, sha: str) -> tuple[bool, str]:
    """把 project_root 恢复到某个快照。

    做法: 先给当前状态打一个 "restore 前自动快照" (免得回滚本身丢了未存的改动),
    再 checkout -f 到目标。返回 (成功, 说明)。
    """
    try:
        if not os.path.isdir(project_root):
            return False, f"项目目录不存在: {project_root}"
        gitdir = _gitdir_for(workspace, project_root)
        if not gitdir.exists():
            return False, "该项目没有任何快照"
        # 回滚前先自保存, 这样"回滚回滚"也可能
        pre = snapshot(workspace, project_root, f"pre-restore-to-{sha}")
        g = _git_env(gitdir, project_root)
        ident = ["-c", "user.name=litecode-snapshot", "-c", "user.email=snap@litecode.local"]
        # 关键: 回滚是"前进到一个内容等于旧快照的新提交", 不是"倒退 HEAD"。
        # 若用 reset --hard 倒退 HEAD, 会切断后续历史 —— 连刚打的 pre-restore
        # 自保存快照也一起丢, 回滚本身就不可回滚了 (违背 pre-restore 的初衷)。
        # 做法: 用 restore 把工作区文件恢复成 sha 的内容 (不动 HEAD),
        # 再 commit 一个新快照接到历史末尾。这样时间线只增不减。
        rc, out = _run(g + ["restore", "--source", sha, "--", "."], cwd=project_root)
        if rc != 0:
            # 老 git 没有 restore 子命令, 退回 checkout <sha> -- .
            rc, out = _run(g + ["checkout", sha, "--", "."], cwd=project_root)
            if rc != 0:
                return False, f"恢复文件失败: {out[:150]}"
        # 清掉 sha 里不存在、又未被任何快照跟踪的未跟踪垃圾 (尊重 .gitignore)
        _run(g + ["clean", "-fd"], cwd=project_root)
        # 把恢复后的状态提交为新快照, 接到历史末尾 (只增不减)
        _run(g + ["add", "-A"], cwd=project_root)
        rc2, out2 = _run(g + ident + ["commit", "--allow-empty", "-q",
                                      "-m", f"[snapshot] restored-to-{sha} @ {time.strftime('%Y-%m-%d %H:%M:%S')}"],
                         cwd=project_root)
        return True, f"已恢复到 {sha}" + (f" (回滚前状态存为 {pre}, 均在历史中可再回)" if pre else "")
    except Exception as exc:
        return False, f"异常: {exc}"


def _prune(workspace: Path, project_root: str) -> None:
    """快照数超上限时不做物理删除 (bare 仓里删 commit 麻烦且易错),
    仅在超出很多时 gc 一次压缩体积。历史保留, 靠 list 的 limit 控制展示。"""
    try:
        gitdir = _gitdir_for(workspace, project_root)
        rc, out = _run(["git", f"--git-dir={gitdir}", "rev-list", "--count", "HEAD"],
                       cwd=project_root)
        if rc == 0 and out.isdigit() and int(out) > _MAX_SNAPSHOTS_PER_PROJECT * 3:
            _run(["git", f"--git-dir={gitdir}", "gc", "--quiet", "--prune=now"], cwd=project_root)
    except Exception:
        pass
