"""handlers/timer.py — create/list/update/delete/run/history 6 个 timer tool."""
from __future__ import annotations

import datetime
from typing import Any

from . import register


def _tm():
    try:
        try:
            from web_ui import _timer_manager  # type: ignore
        except Exception:
            from litecodeext.web_ui import _timer_manager  # type: ignore
        return _timer_manager()
    except Exception as e:
        raise RuntimeError(f"web_ui._timer_manager not importable: {e}")


@register("create_timer")
async def h_create_timer(sid: str, args: dict) -> tuple[str, Any]:
    try:
        mgr = _tm()
    except RuntimeError as e:
        return f"ERROR: {e}", None
    try:
        body = {
            "name": args.get("name", "").strip(),
            "type": args.get("type", "cron"),
            "schedule": args.get("schedule", "").strip(),
            "action_type": args.get("action_type", "shell"),
            "action_target": args.get("action_target", ""),
            "action_content": args.get("action_content", ""),
            "enabled": True,
        }
        timer = mgr.add_timer(body)
        return f"✅ 定时器已创建: id={timer['id']} name={timer['name']} type={timer['type']} schedule={timer['schedule']}", None
    except ValueError as ve:
        return f"ERROR: 创建定时器参数无效: {ve}", None
    except Exception as e:
        return f"ERROR: 创建定时器失败: {e}", None


@register("list_timers")
async def h_list_timers(sid: str, args: dict) -> tuple[str, Any]:
    try:
        mgr = _tm()
    except RuntimeError as e:
        return f"ERROR: {e}", None
    try:
        enabled_only = args.get("enabled_only", False)
        timers = mgr.list_timers()
        if enabled_only:
            timers = [t for t in timers if t.get("enabled")]
        rows = []
        for t in timers:
            last = t.get("last_run")
            last_str = datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if last else "从未"
            rows.append(
                f"id={t['id']} name={t['name']} type={t['type']} "
                f"schedule={t.get('schedule','')} enabled={t.get('enabled')} last_run={last_str}"
            )
        if not rows:
            return "暂无定时器" + ("（已启用）" if enabled_only else ""), None
        return f"共 {len(rows)} 个定时器:\n" + "\n".join(rows), None
    except Exception as e:
        return f"ERROR: list_timers 失败: {e}", None


@register("update_timer")
async def h_update_timer(sid: str, args: dict) -> tuple[str, Any]:
    try:
        mgr = _tm()
    except RuntimeError as e:
        return f"ERROR: {e}", None
    try:
        timer_id = (args.get("id") or "").strip()
        if not timer_id:
            return "ERROR: update_timer 需要 id", None
        patch = {k: args[k] for k in ("name", "schedule", "action_type", "action_content", "enabled")
                 if k in args}
        if not patch:
            return "ERROR: update_timer 未提供任何可修改字段（name/schedule/action_type/action_content/enabled）", None
        result = mgr.update_timer(timer_id, patch)
        if result is None:
            return f"ERROR: 定时器 id={timer_id} 不存在", None
        return f"✅ 定时器已更新: id={timer_id} name={result.get('name')} 改动={list(patch.keys())}", None
    except Exception as e:
        return f"ERROR: update_timer 失败: {e}", None


@register("delete_timer")
async def h_delete_timer(sid: str, args: dict) -> tuple[str, Any]:
    try:
        mgr = _tm()
    except RuntimeError as e:
        return f"ERROR: {e}", None
    try:
        timer_id = (args.get("id") or "").strip()
        if not timer_id:
            return "ERROR: delete_timer 需要 id", None
        ok = mgr.delete_timer(timer_id)
        if not ok:
            return f"ERROR: 定时器 id={timer_id} 不存在", None
        return f"✅ 定时器已删除: id={timer_id}", None
    except Exception as e:
        return f"ERROR: delete_timer 失败: {e}", None


@register("run_timer_now")
async def h_run_timer_now(sid: str, args: dict) -> tuple[str, Any]:
    try:
        mgr = _tm()
    except RuntimeError as e:
        return f"ERROR: {e}", None
    try:
        timer_id = (args.get("id") or "").strip()
        if not timer_id:
            return "ERROR: run_timer_now 需要 id", None
        result = await mgr.run_now(timer_id)
        if not result.get("ok"):
            return f"ERROR: run_timer_now 失败: {result.get('error', '未知')}", None
        return f"✅ 定时器已触发: id={timer_id} result={str(result.get('result',''))[:200]}", None
    except Exception as e:
        return f"ERROR: run_timer_now 失败: {e}", None


@register("get_timer_history")
async def h_get_timer_history(sid: str, args: dict) -> tuple[str, Any]:
    try:
        mgr = _tm()
    except RuntimeError as e:
        return f"ERROR: {e}", None
    try:
        timer_id = (args.get("id") or "").strip()
        limit = int(args.get("limit", 20))
        if not timer_id:
            return "ERROR: get_timer_history 需要 id", None
        timers = mgr.list_timers()
        target = next((t for t in timers if t["id"] == timer_id), None)
        if target is None:
            return f"ERROR: 定时器 id={timer_id} 不存在", None
        history = target.get("history") or []
        history = history[-limit:]
        if not history:
            return f"定时器 id={timer_id} name={target.get('name')} 暂无执行历史", None
        rows = []
        for h in history:
            ts_str = datetime.datetime.fromtimestamp(h.get("ts", 0)).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(
                f"[{ts_str}] status={h.get('status','?')} "
                f"duration={h.get('duration_ms','?')}ms "
                f"result={str(h.get('result',''))[:100]}"
            )
        return (f"定时器 {target.get('name')} (id={timer_id}) 最近 {len(rows)} 条执行记录:\n"
                + "\n".join(rows)), None
    except Exception as e:
        return f"ERROR: get_timer_history 失败: {e}", None
