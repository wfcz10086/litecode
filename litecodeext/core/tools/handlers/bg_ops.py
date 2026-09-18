"""handlers/bg_ops.py — list_bg + kill_bg (session-scoped BG process ops)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from tools.bg import cleanup_session_bg, kill_bg_pid, list_bg_for_session

from . import register


@register("list_bg")
async def h_list_bg(sid: str, args: dict) -> tuple[str, Any]:
    procs = list_bg_for_session(sid or "_orphan")
    if not procs:
        return "No background processes in this session.\n用法: execute_shell(command='...', background=true) 起进程; 起完再 list_bg 看到 + tail_log 看输出.", None
    lines = [f"[BG-PROCESSES in session {sid[:8] if sid else 'orphan'}]"]
    for pid, info in procs.items():
        age = int(time.time() - info.get("ts", 0))
        keep_mark = " [KEEP_ALIVE]" if info.get("keep") else ""
        log_path = info.get("log", "")
        lines.append(
            f"  pid={pid}  age={age}s{keep_mark}\n"
            f"    cmd: {info.get('cmd','?')}\n"
            f"    log: {log_path}"
        )
    lines.append(
        f"\n({len(procs)} procs; "
        f"看日志: tail_log(path='<log>') 或 read_file(filepath='<log>'); "
        f"停: kill_bg(pid=X), 全停 kill_bg(pid='all'))"
    )
    return "\n".join(lines), None


@register("kill_bg")
async def h_kill_bg(sid: str, args: dict) -> tuple[str, Any]:
    pid = args.get("pid", "")
    force = bool(args.get("force", False))
    if pid == "all":
        killed, cmds = await cleanup_session_bg(sid or "_orphan", force=force)
        if killed == 0:
            return "No processes to kill (none running or all marked keep_alive).", None
        return f"Killed {killed} processes:\n" + "\n".join(f"  - {c}" for c in cmds), None
    if not pid:
        return "ERROR: pid required", None
    ok = await kill_bg_pid(pid, 15)
    if not ok:
        await asyncio.sleep(1)
        ok = await kill_bg_pid(pid, 9)
    return (f"Killed pid={pid}" if ok else f"ERROR: kill pid={pid} failed"), None
