"""gateway/sessions.py — sessions / bgprocs / artifacts (/v1/sessions/*, /v1/bgprocs/*)."""
from __future__ import annotations

import asyncio

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
    with _interrupt_lock:
        _interrupt_flags.add(sid)
    log.info(f"[INTERRUPT] flagged session {sid}")
    return {"ok": True, "sid": sid}


@router.get("/v1/bgprocs")
async def list_bgprocs(request: Request):
    """列出后台进程，附带存活状态（用于 web UI 监控 + 重启后清理）。"""
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
        result.append({
            "pid": pid, "alive": alive,
            "sid": sid_key,
            "cmd": info.get("cmd", ""),
            "log": info.get("log", ""),
            "ts": info.get("ts", 0),
            "keep": info.get("keep", False),
        })
    result.sort(key=lambda x: x["ts"], reverse=True)
    return {"procs": result, "count": len(result)}


@router.delete("/v1/bgprocs/{pid}")
async def kill_bgproc(pid: str, request: Request):
    """强杀指定后台进程（用于清理端口占用）。"""
    require_auth(request)
    if not pid.isdigit():
        raise HTTPException(400, "invalid pid")
    try:
        import os as _os
        import signal as _sig
        _os.kill(int(pid), _sig.SIGTERM)
        await asyncio.sleep(0.5)
        if await _check_pid_alive(pid):
            _os.kill(int(pid), _sig.SIGKILL)
        _unregister_bg(pid)
        return {"ok": True, "pid": pid}
    except ProcessLookupError:
        _unregister_bg(pid)
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
