"""
lib/session.py — Session 管理（并发安全 + 磁盘持久化 + 优雅降级）
"""
import json, time, threading, asyncio
from pathlib import Path
from .config import SESSIONS_DISK, SESSION_TTL, WORKSPACE, log

MAX_HISTORY = 500
RECENT_KEEP = 300

_sessions: dict = {}
_slock = threading.Lock()
_session_locks: dict = {}
_slock_dict = threading.Lock()
_interrupt_flags: set = set()
_interrupt_lock = threading.Lock()

# ── Disk I/O (带降级) ────────────────────────────────────────
def _sdisk(sid: str) -> Path:
    return SESSIONS_DISK / f"{sid}.json"

def disk_load(sid: str) -> dict | None:
    p = _sdisk(sid)
    if not p.exists(): return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"[session] disk_load failed for {sid[:8]}: {e}")
        # 优雅降级：文件损坏时尝试备份并返回空
        try:
            bak = p.with_suffix(".json.bak")
            p.rename(bak)
            log.info(f"[session] corrupted file backed up to {bak.name}")
        except Exception:
            pass
        return None

def disk_save(sid: str, msgs: list, artifacts: list = None):
    """原子写入（tmp → rename），防止写一半断电导致文件损坏"""
    p = _sdisk(sid)
    tmp = p.with_suffix(".tmp")
    data = {"sid": sid, "msgs": msgs, "ts": time.time()}
    if artifacts is not None:
        data["artifacts"] = artifacts
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(p)
    except OSError as e:
        log.warning(f"[session] disk_save failed for {sid[:8]}: {e}")

# ── WAL: 逐步增量持久化 (crash-safe) ─────────────────────────
# [FIX 2026-09-04] 旧行为: 整轮 new_msgs 攒在内存, 只在 agent_stream 的 finally
# 里 save_history 一次性落盘。进程中途崩 / 容器重启 → 本轮 reasoning + 工具调用
# 全丢, 磁盘还是旧会话。WAL = 当前进行中这一轮的 append-only 镜像:
#   - 每轮迭代把新增消息 append 进 {sid}.wal (JSONL, 逐行, 崩了丢残缺尾行即可)
#   - 正常收尾 save_history 把整轮压实进 {sid}.json 后 wal_clear
#   - 下次 get_history 若发现 wal 非空 = 上轮崩在提交前 → 回放补回, 不丢步骤
def _swal(sid: str) -> Path:
    return SESSIONS_DISK / f"{sid}.wal"

def wal_append(sid: str, msgs: list):
    """把本轮新增消息增量落盘 (append-only)。空则跳过。"""
    if not sid or not msgs:
        return
    try:
        with _swal(sid).open("a", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
            f.flush()
    except OSError as e:
        log.warning(f"[session] wal_append failed for {sid[:8]}: {e}")

def wal_load(sid: str) -> list:
    """读出未提交的 WAL 消息 (残缺尾行自动丢弃)。"""
    p = _swal(sid)
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # 崩溃写一半的残缺尾行, 丢弃
    except OSError as e:
        log.warning(f"[session] wal_load failed for {sid[:8]}: {e}")
    return out

def wal_clear(sid: str):
    """整轮已压实进 {sid}.json 后清掉 WAL。"""
    try:
        _swal(sid).unlink(missing_ok=True)
    except OSError:
        pass

# ── Session lock ─────────────────────────────────────────────
def get_session_lock(sid: str) -> asyncio.Lock:
    with _slock_dict:
        if sid not in _session_locks:
            _session_locks[sid] = asyncio.Lock()
        return _session_locks[sid]

# ── CRUD ─────────────────────────────────────────────────────
def get_history(sid: str) -> list:
    with _slock:
        if sid not in _sessions:
            disk = disk_load(sid)
            if disk:
                _sessions[sid] = {
                    "msgs": disk.get("msgs", []),
                    "artifacts": disk.get("artifacts", []),
                    "ts": time.time()
                }
                log.info(f"\033[2m  [session:{sid[:8]}] loaded {len(disk.get('msgs',[]))} msgs from disk\033[0m")
            else:
                _sessions[sid] = {"msgs": [], "artifacts": [], "ts": time.time()}
        # [WAL 2026-09-04] 崩溃恢复: 上轮没提交完的 WAL 补回来 (幂等, 补完即清).
        # 只有 server 主循环 / memory 会调本函数, web_ui 走自己的会话 store,
        # 不会在别的轮进行中并发误触, 所以这里 fold+clear 是安全的。
        _pending = wal_load(sid)
        if _pending:
            _sessions[sid]["msgs"] = list(_sessions[sid]["msgs"]) + _pending
            disk_save(sid, _sessions[sid]["msgs"], _sessions[sid].get("artifacts", []))
            wal_clear(sid)
            log.info(f"\033[33m  [session:{sid[:8]}] WAL 恢复 {len(_pending)} 条上轮崩溃消息\033[0m")
        _sessions[sid]["ts"] = time.time()
        return list(_sessions[sid]["msgs"])

def get_artifacts(sid: str) -> list:
    with _slock:
        return list(_sessions.get(sid, {}).get("artifacts", []))

def add_artifact(sid: str, record: dict):
    with _slock:
        if sid not in _sessions: return
        arts = _sessions[sid].setdefault("artifacts", [])
        for i, a in enumerate(arts):
            if a.get("filepath") == record.get("filepath"):
                arts[i] = record
                return
        arts.append(record)

def save_history(sid: str, base_msgs: list, new_turns: list,
                 clear_old_fn=None):
    """
    Delta 合并写入（并发安全）。
    clear_old_fn: 可选的历史清理回调，签名 (msgs, keep_recent) -> msgs
    """
    with _slock:
        disk = disk_load(sid)
        current = disk.get("msgs", list(base_msgs)) if disk else list(base_msgs)
        merged = current + list(new_turns)

        if clear_old_fn:
            merged = clear_old_fn(merged, keep_recent=6)

        l1_path = WORKSPACE / "sessions" / sid / "MEMORY.md"
        keep = RECENT_KEEP if l1_path.exists() else MAX_HISTORY
        trimmed = merged[-keep:]

        disk_arts = disk.get("artifacts", []) if disk else []
        mem_arts = _sessions.get(sid, {}).get("artifacts", [])
        arts_map = {a["filepath"]: a for a in disk_arts}
        for a in mem_arts:
            arts_map[a["filepath"]] = a
        merged_arts = list(arts_map.values())
        _sessions[sid] = {"msgs": trimmed, "artifacts": merged_arts, "ts": time.time()}

    disk_save(sid, trimmed, merged_arts)
    wal_clear(sid)   # [WAL 2026-09-04] 整轮已压实进 {sid}.json, 清掉本轮增量日志

def list_sessions() -> list:
    sids = set()
    with _slock:
        sids.update(_sessions.keys())
    for f in SESSIONS_DISK.glob("*.json"):
        sids.add(f.stem)
    result = []
    for sid in sorted(sids):
        with _slock:
            s = _sessions.get(sid)
        if s:
            result.append({"sid": sid, "msgs": len(s["msgs"]), "ts": s.get("ts", 0)})
        else:
            d = disk_load(sid)
            if d:
                result.append({"sid": sid, "msgs": len(d.get("msgs", [])), "ts": d.get("ts", 0)})
    return result

# ── Session cleanup hook ─────────────────────────
# tool_dispatch 通过 set_cleanup_hook 注入 bg 进程清理函数
_cleanup_hook = None

def set_cleanup_hook(fn):
    """注册 session 结束时的清理回调（由 tool_dispatch 在 init 时调用）"""
    global _cleanup_hook
    _cleanup_hook = fn

def _run_cleanup(sid: str):
    """安全调用清理钩子（同步调用 async 函数）"""
    if not _cleanup_hook:
        return
    try:
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                # 已有 running loop（purge_loop 场景）→ schedule task
                _asyncio.ensure_future(_cleanup_hook(sid))
            else:
                loop.run_until_complete(_cleanup_hook(sid))
        except RuntimeError:
            # 无 loop，新建一个
            _asyncio.new_event_loop().run_until_complete(_cleanup_hook(sid))
    except Exception as e:
        log.warning(f"[session] cleanup hook failed for {sid[:8]}: {e}")

def delete_session(sid: str):
    #  删 session 前先清理关联的 bg 进程
    _run_cleanup(sid)
    with _slock:
        _sessions.pop(sid, None)
    p = _sdisk(sid)
    if p.exists():
        try: p.unlink()
        except OSError: pass

# ── Interrupt ────────────────────────────────────────────────
def set_interrupt(sid: str):
    with _interrupt_lock: _interrupt_flags.add(sid)

def check_interrupt(sid: str) -> bool:
    with _interrupt_lock: return sid in _interrupt_flags

def clear_interrupt(sid: str):
    with _interrupt_lock: _interrupt_flags.discard(sid)

# ── GC ───────────────────────────────────────────────────────
def purge_expired():
    now = time.time()
    with _slock:
        expired = [k for k, v in _sessions.items() if now - v.get("ts", 0) > SESSION_TTL]
        for k in expired:
            _sessions.pop(k, None)
    #  TTL 过期的 session 也清理 bg 进程
    for sid in expired:
        _run_cleanup(sid)

def purge_loop():
    while True:
        time.sleep(300)
        purge_expired()
