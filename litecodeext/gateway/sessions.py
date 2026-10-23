"""gateway/sessions.py — sessions / bgprocs / artifacts (/v1/sessions/*, /v1/bgprocs/*)."""
from __future__ import annotations

import asyncio
import os
import signal
import time

from fastapi import APIRouter, HTTPException, Request

from lib.config import log
from lib.session import (
    _interrupt_flags, _interrupt_lock, _sessions, _slock,
    delete_session as _delete_session,
    disk_load as _disk_load,
    get_artifacts as _get_artifacts,
    SESSIONS_DISK,
)
from tool_dispatch import (  # type: ignore[attr-defined]
    _bg_procs, _bg_lock, _check_pid_alive, _unregister_bg,
)
from tools.bg import bg_exit_code

from ._deps import require_auth

router = APIRouter()


@router.get("/v1/sessions")
async def list_sessions_api(request: Request):
    require_auth(request)
    with _slock:
        items = [{"id": k, "count": len(v["msgs"]), "ts": v["ts"]}
                 for k, v in sorted(_sessions.items(), key=lambda x: x[1]["ts"], reverse=True)]
    disk_ids = {p.stem for p in SESSIONS_DISK.glob("*.json")}
    loaded = {i["id"] for i in items}
    for sid in disk_ids - loaded:
        d = _disk_load(sid)
        if d:
            items.append({"id": sid, "count": len(d.get("msgs", [])), "ts": d.get("ts", 0)})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return {"sessions": items, "count": len(items)}


@router.delete("/v1/sessions/{sid}")
async def delete_session_api(sid: str, request: Request):
    require_auth(request)
    # [v1.0] 原代码调用的 _sdisk 从未导入过, 直接 NameError.
    # 改用已导入的 lib.session.delete_session (_delete_session),
    # 它会处理: bg 进程清理 + _sessions 内存字典 pop + disk 文件 unlink.
    try:
        _delete_session(sid)
    except Exception as e:
        log.warning(f"[delete_session_api] {sid} failed: {e}")
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True}


@router.post("/v1/sessions/{sid}/interrupt")
async def interrupt_session_api(sid: str, request: Request):
    """标记 session 中断，agent 循环在下一轮迭代时会检测并退出。"""
    require_auth(request)
    from lib.session import set_interrupt
    set_interrupt(sid)          # 打标记 + 停后台子代理 + 停本会话的后台进程 (keep_alive 的不动)
    log.info(f"[INTERRUPT] flagged session {sid}")
    return {"ok": True, "sid": sid}


@router.post("/v1/sessions/{sid}/steer")
async def steer_session_api(sid: str, request: Request):
    """运行中插话: 回合进行中才排队, 由 agent 循环在两步之间接进上下文; 没有进行中的回合返回 queued=False。"""
    require_auth(request)
    from lib.session import _session_locks, push_steer
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty")
    lock = _session_locks.get(sid)
    if not (lock and lock.locked()):
        return {"ok": True, "queued": False}
    urgent = bool(body.get("urgent"))
    push_steer(sid, text[:4000], urgent=urgent)
    log.info(f"[STEER] {sid} {'立即' if urgent else '排队'}: {text[:60]}")
    return {"ok": True, "queued": True, "urgent": urgent}


# [bgcard 2026-10-23] 刚结束的后台任务再保留 10 分钟 (alive=False + exit_code), 聊天窗口的
# 「后台进行中」卡片才能显示 "已完成 / 失败 (code=N)" 并看最后日志; 原来进程一死就从登记表删掉,
# 只有恰好那一次轮询能看到, tail 接口也立刻 404。字段只增不改, 老调用方不受影响。
RECENT_KEEP_S = 600
_bg_recent: dict = {}   # pid → 列表项 (alive=False)


def _remember_exit(sid_key, pid, info, stopped=False):
    log_path = info.get("log", "")
    _bg_recent[str(pid)] = {
        "pid": str(pid), "alive": False, "sid": sid_key,
        "cmd": info.get("cmd", ""), "log": log_path,
        "ts": info.get("ts", 0), "keep": info.get("keep", False),
        "exit_code": bg_exit_code(log_path) if log_path else None,
        "stopped": stopped, "ended": time.time(),
    }


def _find_bg(pid):
    with _bg_lock:
        for sid_key, procs in _bg_procs.items():
            if pid in procs:
                return sid_key, dict(procs[pid])
    return None, None


@router.get("/v1/bgprocs")
async def list_bgprocs(request: Request):
    """列出后台进程，附带存活状态（用于 web UI 监控 + 重启后清理）。
    已结束的任务保留 RECENT_KEEP_S 秒: alive=False, exit_code=日志里的 [BG-EXIT code=N] (没有则 None),
    stopped=是否用户手动停止, ended=结束时间。"""
    require_auth(request)
    # [v1.0] _bg_procs 结构变为 {sid: {pid: info}}, 此处 flatten 展开
    with _bg_lock:
        flat = []
        for sid_key, procs in _bg_procs.items():
            for pid, info in procs.items():
                flat.append((sid_key, pid, dict(info)))
    result = []
    for sid_key, pid, info in flat:
        alive = await _check_pid_alive(pid)
        if not alive:
            _unregister_bg(pid)
            _remember_exit(sid_key, pid, info)
            continue
        result.append({
            "pid": pid, "alive": alive,
            "sid": sid_key,
            "cmd": info.get("cmd", ""),
            "log": info.get("log", ""),
            "ts": info.get("ts", 0),
            "keep": info.get("keep", False),
            "exit_code": None,
        })
    now = time.time()
    for pid, e in list(_bg_recent.items()):
        if now - e.get("ended", 0) > RECENT_KEEP_S:
            _bg_recent.pop(pid, None)
        elif not any(r["pid"] == pid for r in result):
            result.append(dict(e))
    result.sort(key=lambda x: x["ts"], reverse=True)
    return {"procs": result, "count": len(result)}


@router.delete("/v1/bgprocs/{pid}")
async def kill_bgproc(pid: str, request: Request):
    """强杀指定后台进程（用于清理端口占用 / 聊天窗口「停止」）。
    后台任务都以 start_new_session 启动、自成进程组: 按组发信号, 否则只杀掉外层 bash,
    真正干活的子进程 (python 生成视频等) 变成孤儿继续跑。"""
    require_auth(request)
    if not pid.isdigit():
        raise HTTPException(400, "invalid pid")
    sid_key, info = _find_bg(pid)

    def _sig(s):
        try:
            pgid = os.getpgid(int(pid))
            if pgid != os.getpgid(0):
                os.killpg(pgid, s)
                return
        except (ProcessLookupError, PermissionError):
            pass
        os.kill(int(pid), s)

    try:
        _sig(signal.SIGTERM)
        await asyncio.sleep(0.5)
        if await _check_pid_alive(pid):
            _sig(signal.SIGKILL)
        _unregister_bg(pid)
        if info is not None:
            _remember_exit(sid_key, pid, info, stopped=True)
        return {"ok": True, "pid": pid}
    except ProcessLookupError:
        _unregister_bg(pid)
        if info is not None:
            _remember_exit(sid_key, pid, info, stopped=True)
        return {"ok": True, "pid": pid, "note": "already gone"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/v1/sessions/{sid}/artifacts")
async def get_artifacts_api(sid: str, request: Request):
    """返回 session 内创建/修改的文件列表（artifact 记录）。"""
    require_auth(request)
    # 先尝试内存，再从磁盘加载
    arts = _get_artifacts(sid)
    if not arts:
        disk = _disk_load(sid)
        if disk:
            arts = disk.get("artifacts", [])
    # 按修改时间降序
    arts = sorted(arts, key=lambda a: a.get("ts", 0), reverse=True)
    return {"sid": sid, "artifacts": arts, "count": len(arts)}
