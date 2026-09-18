"""handlers/task_ops.py — 会话内任务追踪 (task_create/list/update/done).

任务存在内存 dict, 按 sid 隔离。agent 可自己建任务、更新状态、查进度, 像 Claude Code 的 TaskCreate/TaskUpdate。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from . import register

# sid -> {task_id -> task_dict}
_STORE: dict[str, dict[str, dict]] = {}

_VALID_STATUS = {"pending", "in_progress", "completed", "blocked"}


def _store(sid: str) -> dict[str, dict]:
    if sid not in _STORE:
        _STORE[sid] = {}
    return _STORE[sid]


@register("task_create")
async def h_task_create(sid: str, args: dict) -> tuple[str, Any]:
    title = (args.get("title") or "").strip()
    description = args.get("description") or ""
    priority = args.get("priority") or "normal"
    if not title:
        return "ERROR: task_create 需要 title", None
    tid = uuid.uuid4().hex[:8]
    task = {
        "id": tid,
        "title": title,
        "description": description,
        "priority": priority,
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "notes": [],
    }
    _store(sid)[tid] = task
    return f"[task_create] #{tid}: {title} (pending)", None


@register("task_list")
async def h_task_list(sid: str, args: dict) -> tuple[str, Any]:
    status_filter = args.get("status") or ""
    tasks = _store(sid)
    if not tasks:
        return "[task_list] 无任务", None
    lines = []
    for t in tasks.values():
        if status_filter and t["status"] != status_filter:
            continue
        badge = {"pending": "⏳", "in_progress": "🔄", "completed": "✅", "blocked": "🚫"}.get(t["status"], "?")
        lines.append(f"{badge} #{t['id']} [{t['status']}] {t['title']}")
        if t.get("notes"):
            lines.append(f"   └ {t['notes'][-1]}")
    return "[task_list]\n" + "\n".join(lines) if lines else "[task_list] (无匹配)", None


@register("task_update")
async def h_task_update(sid: str, args: dict) -> tuple[str, Any]:
    tid = (args.get("id") or "").strip()
    status = (args.get("status") or "").strip()
    note = (args.get("note") or "").strip()
    tasks = _store(sid)
    if not tid:
        return "ERROR: task_update 需要 id", None
    if tid not in tasks:
        return f"ERROR: task #{tid} 不存在", None
    if status:
        if status not in _VALID_STATUS:
            return f"ERROR: status 只能是 {_VALID_STATUS}", None
        tasks[tid]["status"] = status
    if note:
        tasks[tid]["notes"].append(note)
    tasks[tid]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    t = tasks[tid]
    return f"[task_update] #{tid} → {t['status']}" + (f" | {note}" if note else ""), None


@register("task_done")
async def h_task_done(sid: str, args: dict) -> tuple[str, Any]:
    tid = (args.get("id") or "").strip()
    note = args.get("note") or "完成"
    tasks = _store(sid)
    if not tid or tid not in tasks:
        return f"ERROR: task #{tid} 不存在", None
    tasks[tid]["status"] = "completed"
    tasks[tid]["notes"].append(note)
    tasks[tid]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"[task_done] #{tid} ✅ {tasks[tid]['title']}", None
