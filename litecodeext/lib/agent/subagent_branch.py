"""lib/agent/subagent_branch.py — Web 子代理面板的分支操作: 看改动 / 合并 / 丢弃。

只对"本会话派出的、挂了独立工作树的"子代理生效 (调用方先经 registry.get(sid, rid) 做会话隔离),
工作树 id 一律取自登记记录 (rec.worktree_id), 不接受前端传入。底层复用 subagent_worktree:
  diff     base..branch (分支已删时用记下的 commit) + 工作树里未提交的改动; 总长上限 DIFF_MAX 字节
  merge    合到发起时的基分支 (create_worktree 记下的 base_branch): 主工作区当前不在该分支 / 有未提交改动 /
           子代理还在跑 → 拒绝; 冲突 → _merge_sync 已 merge --abort, 不留半合并状态, 返回冲突文件
  discard  删工作树与分支 (agent_worktree discard)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import subagent_events as _ev
from . import subagent_worktree as wt

DIFF_MAX = 200 * 1024
_FINAL = {"merged", "discarded", "no_changes"}


def _meta(rec):
    wid = getattr(rec, "worktree_id", "") or ""
    if not wid:
        return None, "这个子代理没有独立工作树 (没有改动分支可操作)"
    m = wt.load_meta(wid)
    if not m:
        return None, f"工作树 {wid} 的记录不存在 (可能已被清理)"
    return m, ""


def _diff_sync(m: dict) -> dict:
    repo, base = m.get("repo"), m.get("base")
    ref = m.get("branch") or ""
    if not ref or wt._git(["rev-parse", "--verify", "--quiet", ref], repo).returncode != 0:
        ref = m.get("commit") or ""             # 分支已合并删除 / 丢弃: 用记下的提交
        if not ref and m.get("merge_commit"):   # 子代理自己 commit 过 (框架没补提交) → 合并提交的第二父 = 分支末端
            ref = f"{m['merge_commit']}^2"
    out, files = "", []
    if ref and base and wt._git(["cat-file", "-e", ref], repo).returncode == 0:
        out = wt._git(["diff", f"{base}..{ref}", "--", "."] + wt.JUNK_PATHSPEC, repo).stdout
        files = wt._git(["diff", "--name-only", f"{base}..{ref}", "--", "."] + wt.JUNK_PATHSPEC, repo).stdout.split()
    p = m.get("path")
    if p and Path(p).is_dir():                 # 运行中 / 未提交: 工作树里的改动 (含新文件)
        wt._git(["add", "-N", "--", "."] + wt.JUNK_PATHSPEC, p)
        unc = wt._git(["diff", "HEAD", "--", "."] + wt.JUNK_PATHSPEC, p).stdout
        if unc:
            out += unc
            files += [f for f in wt._git(["diff", "--name-only", "HEAD", "--", "."] + wt.JUNK_PATHSPEC, p).stdout.split()
                      if f not in files]
    raw = out.encode("utf-8", "replace")
    truncated = len(raw) > DIFF_MAX
    if truncated:
        out = raw[:DIFF_MAX].decode("utf-8", "ignore")
    return {"diff": out, "files": files, "truncated": truncated, "bytes": len(raw)}


async def diff(rec) -> dict:
    m, err = _meta(rec)
    if m is None:
        return {"ok": False, "error": err}
    d = await asyncio.to_thread(_diff_sync, m)
    return {"ok": True, "worktree": _ev.worktree_info(rec), **d}


def _current_branch(repo) -> str:
    r = wt._git(["symbolic-ref", "--short", "-q", "HEAD"], repo)
    return r.stdout.strip() if r.returncode == 0 else ""


async def merge(rec) -> dict:
    m, err = _meta(rec)
    if m is None:
        return {"ok": False, "error": err}
    if rec.status == "running":
        return {"ok": False, "error": "子代理还在运行, 结束后再合并 (或先停止它)"}
    st = m.get("status")
    if st in _FINAL:
        return {"ok": False, "error": f"工作树已是「{wt._STATUS_CN.get(st, st)}」状态, 无需合并"}
    repo = m.get("repo") or ""
    if not repo or not Path(repo).is_dir():
        return {"ok": False, "error": f"原仓库 {repo or '?'} 不存在"}
    base_branch = m.get("base_branch") or ""
    now = await asyncio.to_thread(_current_branch, repo)
    if base_branch and now != base_branch:
        return {"ok": False, "error": f"主工作区当前在 {now or '游离 HEAD'}, 不是发起时的基分支 {base_branch}; "
                                      "请先切回再合并 (不会自动切换分支)"}
    await wt.agent_worktree_tool({"action": "merge", "id": m["id"]})
    m = wt.load_meta(m["id"]) or m
    info = _ev.worktree_info(rec)
    _ev.emit(rec, "worktree", **(info or {}))
    if m.get("status") == "merged":
        _ev.emit(rec, "notice", text=f"已合并到 {base_branch or '主工作区'} ({(m.get('merge_commit') or '')[:10]})",
                 level="info")
        return {"ok": True, "message": f"已合并到 {base_branch or '主工作区'}", "merge_commit": m.get("merge_commit"),
                "worktree": info}
    conflicts = m.get("conflicts") or []
    msg = (f"合并冲突, 已撤销合并 (未留下半合并状态): {', '.join(conflicts)}" if m.get("status") == "conflict" and conflicts
           else (m.get("note") or f"合并失败 (状态 {m.get('status')})"))
    _ev.emit(rec, "notice", text=msg[:200], level="warn")
    return {"ok": False, "error": msg, "conflicts": conflicts, "worktree": info}


async def discard(rec) -> dict:
    m, err = _meta(rec)
    if m is None:
        return {"ok": False, "error": err}
    if rec.status == "running":
        return {"ok": False, "error": "子代理还在运行, 先停止再丢弃"}
    if m.get("status") in _FINAL:
        return {"ok": False, "error": f"工作树已是「{wt._STATUS_CN.get(m['status'], m['status'])}」状态, 无需丢弃"}
    await wt.agent_worktree_tool({"action": "discard", "id": m["id"]})
    info = _ev.worktree_info(rec)
    _ev.emit(rec, "worktree", **(info or {}))
    _ev.emit(rec, "notice", text=f"已丢弃改动: 删除分支 {m.get('branch')} 与工作树", level="info")
    return {"ok": True, "message": f"已丢弃: 分支 {m.get('branch')} 与工作树已删除", "worktree": info}
