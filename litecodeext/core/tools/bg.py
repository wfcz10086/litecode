"""core/tools/bg.py — background-process registry + lifecycle.

Provides the shared `_bg_procs` dict (session_id → {pid → info}) plus:
- register / unregister / persist / restore
- kill_bg_pid / cleanup_session_bg
- _check_pid_alive (async kill -0)
- _exec_bg — launch long-lived nohup process, optionally polling health_url

CRITICAL: `_bg_procs` and `_bg_lock` are MODULE-LEVEL and must NEVER be
reassigned (`global _bg_procs; _bg_procs = ...`) because other modules
import them by-name via `from tools.bg import _bg_procs`. Any restore
uses in-place mutation (`_bg_procs.clear(); _bg_procs.update(...)`).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path

import httpx

from lib.config import WORKSPACE, log

# ── BG process registry: track all spawned pids (for cleanup on restart) ─────
# [v1.0] 按 session_id 组织，session 结束时自动 kill 该 session 启动的进程
# 结构: {sid: {pid: {"cmd": str, "log": str, "ts": float, "keep": bool}}}
# keep=True 表示明确要求保留（用户要求长跑服务），默认 False = session 结束时清理
_bg_procs: dict = {}
_bg_lock  = threading.Lock()


def _bg_state_path() -> Path:
    """BG registry 持久化文件，server 重启后可恢复"""
    return WORKSPACE / ".bg_procs.json"


def _persist_bg_state():
    """把 _bg_procs 写到磁盘，server 重启可恢复"""
    try:
        _bg_state_path().write_text(json.dumps(_bg_procs, ensure_ascii=False))
    except Exception:
        pass


def _restore_bg_state():
    """server 启动时扫描 .bg_procs.json 恢复 registry.

    IMPORTANT: 用 clear()/update() 原地修改, 不能 `_bg_procs = json.loads(...)`,
    否则 `from tools.bg import _bg_procs` 拿到的老引用会与新 dict 脱钩.
    """
    try:
        p = _bg_state_path()
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                with _bg_lock:
                    _bg_procs.clear()
                    _bg_procs.update(data)
                log.info(f"  [bg] restored {sum(len(v) for v in _bg_procs.values())} bg procs from state file")
    except Exception as e:
        log.warning(f"  [bg] failed to restore state: {e}")


def _register_bg(pid: str, cmd: str, log_file, sid: str = "_orphan", keep: bool = False):
    """[v1.0] pid 关联到 session；keep=True 表示要长期保留"""
    with _bg_lock:
        sid_key = sid or "_orphan"
        _bg_procs.setdefault(sid_key, {})[pid] = {
            "cmd": cmd[:120], "log": str(log_file), "ts": time.time(), "keep": keep,
        }
    _persist_bg_state()


def _unregister_bg(pid: str):
    """从 registry 移除（遍历所有 session 找 pid）"""
    with _bg_lock:
        for sid_key, procs in list(_bg_procs.items()):
            if pid in procs:
                procs.pop(pid, None)
                if not procs:
                    _bg_procs.pop(sid_key, None)
                break
    _persist_bg_state()


def list_bg_for_session(sid: str) -> dict:
    """列出某 session 的所有 bg 进程"""
    with _bg_lock:
        return dict(_bg_procs.get(sid or "_orphan", {}))


async def kill_bg_pid(pid: str, signal: int = 15) -> bool:
    """kill 单个 pid（SIGTERM=15, SIGKILL=9）"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "kill", f"-{signal}", pid,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=3)
        _unregister_bg(pid)
        return proc.returncode == 0
    except Exception:
        return False


async def cleanup_session_bg(sid: str, force: bool = False) -> tuple[int, list]:
    """
    [v1.0] session 结束时清理该 session 的 bg 进程（除非 keep=True）。
    返回: (killed_count, killed_cmds)
    """
    with _bg_lock:
        procs = dict(_bg_procs.get(sid or "_orphan", {}))
    if not procs:
        return 0, []
    killed = []
    for pid, info in procs.items():
        if info.get("keep") and not force:
            continue
        # 先 SIGTERM，再 SIGKILL
        if await kill_bg_pid(pid, 15):
            killed.append(info.get("cmd", "?")[:60])
        else:
            # 给 1 秒然后 SIGKILL
            await asyncio.sleep(1)
            await kill_bg_pid(pid, 9)
            killed.append(info.get("cmd", "?")[:60] + "(force)")
    if killed:
        log.info(f"  [bg-cleanup] session {sid[:8] if sid else 'orphan'}: killed {len(killed)} procs")
    return len(killed), killed


async def _check_pid_alive(pid: str) -> bool:
    """Non-blocking async pid-alive check via kill -0."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "kill", "-0", pid,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=2)
        return proc.returncode == 0
    except Exception:
        return True   # 查不到就假设还活着，防止误杀


async def _exec_bg(cmd: str, health_url: str = None, sid: str = None, keep: bool = False) -> str:
    """
    Launch a long-lived background process via nohup.

    [v1.0] 新增 sid 参数: 进程关联到 session，session 结束时自动清理。
                keep=True: 明确要求长期保留（不被 session cleanup 杀掉）

    关键修复（相比原版）：
    1. 用 asyncio.create_subprocess_shell 替代阻塞的 subprocess.run
    2. kill -0 存活检查改用 async subprocess，不再阻塞 event loop。
    3. 新增 PID registry：server 重启时可清查残留进程。
    4. 新增 start_new_session=True：nohup 进程与 server 的 process group 隔离。
    """
    tag      = uuid.uuid4().hex[:6]
    log_file = WORKSPACE / f"bg_{tag}.log"
    pid_file = WORKSPACE / f"bg_{tag}.pid"

    # [FIX 2026-05] 之前用 proc.communicate() 等 shell stdout EOF, 但 chrome 类多 fork
    # 子进程会持有原 shell 的 stdout fd 不让关 → 10s 超时. 改用 readline 只取 PID 行,
    # 拿到 PID 就走, 不等 shell 完整结束.
    # </dev/null 关闭 stdin 防止 chrome 等 stdin.
    bg_cmd = f"nohup {cmd} </dev/null >{log_file} 2>&1 & echo $!"
    try:
        proc = await asyncio.create_subprocess_shell(
            bg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(WORKSPACE),
            start_new_session=True,
        )
        # 只读一行 (PID), 5s 内一般立即 echo $!
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=8)
        pid = line.decode(errors="replace").strip()
        if not pid.isdigit():
            return f"ERROR: unexpected nohup output (no pid): {pid!r}"
        pid_file.write_text(pid)
        _register_bg(pid, cmd, log_file, sid=sid, keep=keep)
        # 让 shell 在后台自己退出, 不阻塞主流程
    except asyncio.TimeoutError:
        return "ERROR: nohup PID readline timed out after 8s (shell 自己没 echo PID, 检查 cmd 是否合法)"
    except Exception as e:
        return f"ERROR starting background process: {e}"

    def _read_log() -> str:
        try:
            return log_file.read_text(errors="replace") if log_file.exists() else "(no log)"
        except Exception:
            return "(log unreadable)"

    if not health_url:
        await asyncio.sleep(2)
        proc_log = _read_log()
        return (
            f"Background started pid={pid}\n"
            f"log_file={log_file}\n"
            f"--- stdout/stderr ---\n{proc_log[:800]}"
        )

    # Poll health URL; also check if process died early
    for i in range(30):
        await asyncio.sleep(1)

        # ★ async kill -0 — 不再阻塞 event loop
        alive = await _check_pid_alive(pid)
        if not alive:
            proc_log = _read_log()
            _unregister_bg(pid)
            # [FIX] 日志为空时 (nohup > /dev/null 或重定向被覆盖) 做 fallback:
            # 同步重跑一次 timeout 5 <cmd> 2>&1 捕获 stderr, 让 agent 看到真实错误
            _diag = proc_log
            _stripped = proc_log.strip() if proc_log else ""
            if not _stripped or _stripped == "(no log)" or _stripped == "(log unreadable)" or len(_stripped) < 40:
                try:
                    _cmd_diag = cmd.rstrip()
                    while _cmd_diag.endswith("&"):
                        _cmd_diag = _cmd_diag[:-1].rstrip()
                    import re as _re_d
                    _cmd_diag = _re_d.sub(r"^\s*nohup\s+", "", _cmd_diag)
                    _cmd_diag = _re_d.sub(r"\s*>\s*\S+\s*2>&1\s*$", "", _cmd_diag)
                    _cmd_diag = _re_d.sub(r"\s*>\s*\S+\s*2>\s*\S+\s*$", "", _cmd_diag)
                    _dp = await asyncio.create_subprocess_shell(
                        f"timeout 5 {_cmd_diag} 2>&1",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=str(WORKSPACE),
                    )
                    _dout, _ = await asyncio.wait_for(_dp.communicate(), timeout=8)
                    _dout_s = _dout.decode(errors="replace")
                    if _dout_s.strip():
                        _diag = f"(原 log 为空, 已 strip 末尾 & + nohup/重定向, 重跑 5s 捕获如下)\n{_dout_s}"
                    else:
                        _diag = f"(原 log 为空, 同步重跑 5s 也无输出. 可能进程根本没启动. cmd_diag={_cmd_diag!r})"
                except Exception as _de:
                    _diag += f"\n(重跑也失败: {_de})"
            return (
                f"ERROR: Background process pid={pid} died after {i+1}s\n"
                f"cmd: {cmd[:200]}\n"
                f"log_file: {log_file}\n"
                f"--- process log (diagnose and fix) ---\n{_diag[-1800:]}"
            )

        try:
            async with httpx.AsyncClient(timeout=2) as c:
                res = await c.get(health_url)
                if res.status_code < 500:
                    proc_log = _read_log()
                    return (
                        f"Background ready pid={pid} after {i+1}s\n"
                        f"--- startup log ---\n{proc_log[:400]}"
                    )
        except Exception:
            pass

    # Timeout: return full log so model can self-diagnose
    proc_log = _read_log()
    return (
        f"ERROR: health_url={health_url} not responding after 30s (pid={pid})\n"
        f"--- process log (read carefully, diagnose the failure) ---\n{proc_log[-2000:]}\n"
        f"HINT: Do NOT retry background=true again. Instead run in foreground first to see the actual error:\n"
        f"  execute_shell('cd <project_dir> && timeout 5 python3 main.py 2>&1')\n"
        f"Fix the error, then try background=true again (max 1 more attempt)."
    )
