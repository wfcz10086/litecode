"""handlers/git_ops.py — git 操作工具 (status/diff/log/commit/checkout/stash)."""
from __future__ import annotations

import asyncio
import shlex
from typing import Any

from . import register


async def _run(cmd: str, cwd: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or None,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise TimeoutError(f"git command timed out ({timeout}s): {cmd}")
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def _cap(s: str, n: int = 4000) -> str:
    return s[:n] + ("\n...(truncated)" if len(s) > n else "")


@register("git_status")
async def h_git_status(sid: str, args: dict) -> tuple[str, Any]:
    cwd = args.get("cwd") or "."
    rc, out, err = await _run("git status --short --branch", cwd=cwd)
    if rc != 0:
        return f"ERROR: git status rc={rc}\n{err[:500]}", None
    return f"[git status @ {cwd}]\n{out or '(clean)'}", None


@register("git_diff")
async def h_git_diff(sid: str, args: dict) -> tuple[str, Any]:
    cwd = args.get("cwd") or "."
    staged = args.get("staged", False)
    path = args.get("path") or ""
    flag = "--cached" if staged else ""
    target = shlex.quote(path) if path else ""
    rc, out, err = await _run(f"git diff {flag} {target}", cwd=cwd)
    if rc != 0:
        return f"ERROR: git diff rc={rc}\n{err[:500]}", None
    return f"[git diff {'--cached ' if staged else ''}@ {cwd}]\n{_cap(out) or '(no diff)'}", None


@register("git_log")
async def h_git_log(sid: str, args: dict) -> tuple[str, Any]:
    cwd = args.get("cwd") or "."
    n = int(args.get("n") or 20)
    rc, out, err = await _run(
        f"git log --oneline --decorate -n {n}", cwd=cwd
    )
    if rc != 0:
        return f"ERROR: git log rc={rc}\n{err[:500]}", None
    return f"[git log -n{n} @ {cwd}]\n{out or '(empty)'}", None


@register("git_commit")
async def h_git_commit(sid: str, args: dict) -> tuple[str, Any]:
    cwd = args.get("cwd") or "."
    message = (args.get("message") or "").strip()
    add_all = args.get("add_all", False)
    files = args.get("files") or []  # list of paths to stage
    if not message:
        return "ERROR: git_commit 需要 message", None
    cmds = []
    if add_all:
        cmds.append("git add -A")
    elif files:
        quoted = " ".join(shlex.quote(f) for f in files)
        cmds.append(f"git add {quoted}")
    safe_msg = message.replace("'", "'\\''")
    cmds.append(f"git commit -m '{safe_msg}'")
    rc, out, err = await _run(" && ".join(cmds), cwd=cwd)
    combined = (out + err).strip()
    if rc != 0:
        return f"ERROR: git_commit rc={rc}\n{_cap(combined)}", None
    return f"[git commit @ {cwd}]\n{_cap(combined)}", None


@register("git_checkout")
async def h_git_checkout(sid: str, args: dict) -> tuple[str, Any]:
    cwd = args.get("cwd") or "."
    branch = (args.get("branch") or "").strip()
    create = args.get("create", False)
    if not branch:
        return "ERROR: git_checkout 需要 branch", None
    flag = "-b" if create else ""
    rc, out, err = await _run(f"git checkout {flag} {shlex.quote(branch)}", cwd=cwd)
    combined = (out + err).strip()
    if rc != 0:
        return f"ERROR: git_checkout rc={rc}\n{combined[:500]}", None
    return f"[git checkout {branch} @ {cwd}]\n{combined}", None


@register("git_stash")
async def h_git_stash(sid: str, args: dict) -> tuple[str, Any]:
    cwd = args.get("cwd") or "."
    action = (args.get("action") or "push").lower()  # push|pop|list|drop
    if action not in ("push", "pop", "list", "drop"):
        return "ERROR: git_stash action 只能是 push/pop/list/drop", None
    rc, out, err = await _run(f"git stash {action}", cwd=cwd)
    combined = (out + err).strip()
    if rc != 0:
        return f"ERROR: git_stash {action} rc={rc}\n{combined[:500]}", None
    return f"[git stash {action} @ {cwd}]\n{combined or '(ok)'}", None
