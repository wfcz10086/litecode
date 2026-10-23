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
import os
import re
import shlex
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



BG_EXIT_MARK = "[BG-EXIT code="


def _as_bash(cmd: str, mark_exit: bool = False) -> str:
    """把模型给的整条命令交给 bash -c 执行。
    nohup/timeout 只能 exec 可执行文件: `nohup cd x && y` 里 nohup 去执行 cd 必然失败,
    `&&` 后面的服务永远起不来。末尾的 `&` 和开头的 nohup 由调用方负责, 这里剥掉。
    mark_exit=True: 命令结束时往日志末尾追加一行 `[BG-EXIT code=N]`, 等后台任务的人
    grep 这行就知道"跑完了没、成没成", 不用 pgrep (pgrep -f 会匹配到等待命令自己, 永远等不到)。"""
    c = cmd.strip()
    while c.endswith("&") and not c.endswith("&&"):
        c = c[:-1].rstrip()
    c = re.sub(r"^nohup\s+", "", c)
    if mark_exit:
        # trap EXIT 而不是在末尾 echo: `set -e` 失败 / 中途 `exit 3` 时末尾那行根本跑不到, 等的人要空等 900s
        c = "trap 'echo \"" + BG_EXIT_MARK + "$?]\"' EXIT\n" + c
    return f"bash -c {shlex.quote(c)}"


def bg_exit_code(log_path) -> "int | None":
    """日志里有 [BG-EXIT code=N] → N; 还在跑 / 老进程没这行 → None"""
    try:
        p = Path(log_path)
        sz = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, sz - 4096))
            tail = f.read().decode(errors="replace")
        m = re.findall(r"\[BG-EXIT code=(\d+)\]", tail)
        return int(m[-1]) if m else None
    except Exception:
        return None


def wait_hint(log_file) -> str:
    """起后台任务时告诉模型怎么等它结束 (一条前台命令, 不轮询、不 pgrep)"""
    return (f"预计几分钟内结束、且本轮回答必须用到它的结果时才等: execute_shell(command=\"until grep -q 'BG-EXIT' {log_file}; "
            f"do sleep 5; done; tail -20 {log_file}\", timeout=900) —— 最后一行 [BG-EXIT code=0] 是成功, 非 0 是失败。"
            f"**预计超过 5 分钟的 (生成视频/MV、批量出图、长翻译、训练等) 不要在本轮等**: 先把已经做好的东西交付, "
            f"告诉用户预计多久、问进度时会查 `tail {log_file}`, 然后结束本轮 —— 一直等会堵住对话, 用户的新消息只能排队。"
            f"别用 pgrep -f 判断是否结束 (会匹配到等待命令自己)。")

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
            "cmd": cmd[:300], "log": str(log_file), "ts": time.time(), "keep": keep,
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
    """结束后台进程（SIGTERM=15, SIGKILL=9）。
    每次 _exec_bg 都以 start_new_session 启动, 自成一个进程组; 按组发信号,
    npm/uvicorn --reload 这类再 fork 子进程的命令才不会留下占端口的孤儿。"""
    try:
        pgid = os.getpgid(int(pid))
        if pgid != os.getpgid(0):
            os.killpg(pgid, signal)
            _unregister_bg(pid)
            return True
    except (ProcessLookupError, PermissionError, ValueError):
        pass
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


async def _exec_bg(cmd: str, health_url: str = None, sid: str = None, keep: bool = False,
                   cwd=None) -> str:
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
    bg_cmd = f"nohup {_as_bash(cmd, mark_exit=not keep and not health_url)} </dev/null >{log_file} 2>&1 & echo $!"
    try:
        proc = await asyncio.create_subprocess_shell(
            bg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(cwd or WORKSPACE),   # [worktree-iso] 调用方给的 cwd (子代理工作树)
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
            + ("" if keep else wait_hint(log_file) + "\n") +
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
            # [2026-10-19] 不再"同步重跑 5s 捕获 stderr": 原来在 WORKSPACE (而不是启动时的 cwd) 里
            # 重跑模型的命令, 相对路径全错 → 报出与真实原因无关的误导性错误; 而且会把非幂等命令
            # (迁移/写库/发请求) 再执行一遍。现在只如实报告日志, 日志为空就明说。
            _diag = proc_log
            _stripped = proc_log.strip() if proc_log else ""
            if not _stripped or _stripped in ("(no log)", "(log unreadable)"):
                _diag = ("(日志为空: 进程没输出任何内容就退出了。可能命令本身立即失败 / 输出被重定向到别处 / "
                         f"启动目录不对 (启动目录 {cwd or WORKSPACE})。前台直接跑一次同样的命令看报错)")
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
    # [2026-10-19] 原提示写死 "python3 main.py" 并说"不要重试 background", 慢启动服务 (模型加载/编译)
    # 30s 没就绪其实还在正常启动, 模型却被引导去杀进程、换前台重跑 → 来回空转。
    # 进程还活着 → 说明仍在启动, 给日志路径和等待方法; 已死 → 才按失败处理。
    proc_log = _read_log()
    if await _check_pid_alive(pid):
        return (
            f"Background still starting pid={pid}: health_url={health_url} 30s 内未就绪, 但进程仍在运行 (不是失败)。\n"
            f"log_file={log_file}\n"
            f"等它就绪: execute_shell(command=\"until curl -s -o /dev/null {health_url}; do "
            f"kill -0 {pid} 2>/dev/null || {{ echo '[BG-EXIT] pid {pid} 已退出'; break; }}; sleep 3; done; "
            f"tail -20 {log_file}\", timeout=600) —— 别重复启动同一个服务 (端口会冲突)。\n"
            f"--- 当前日志 ---\n{proc_log[-1500:]}"
        )
    _unregister_bg(pid)
    return (
        f"ERROR: health_url={health_url} not responding after 30s, 且进程 pid={pid} 已退出\n"
        f"log_file: {log_file}\n"
        f"--- process log (read carefully, diagnose the failure) ---\n{proc_log[-2000:]}"
    )
