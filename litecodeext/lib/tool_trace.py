"""lib/tool_trace.py — 对话里的「调用链路」: 工具结果事件 / 进度 / 说明, 以及落库与重绘用的整理。

SSE (都在 choices[0].delta 里, 全部可选, 老客户端忽略即可; 字段说明见 docs/SSE_CONTRACT.md §7.7):
    task_exec   executing 帧新增可选 id / name / args, 与 tool_done.id 对应同一次调用
    tool_done   {id, name, ok, duration_ms, summary, error_kind?, error_title?, error_hint?, retry_of?}
    tool_progress {id, name, text, pct?}   长任务进度 / 自动降级说明 ("搜索源 A 失败, 改用 B")
    notice      {text, level, kind}        整轮级的自动重试 / 切换说明 ("模型服务 503, 已自动重试 1/3")

落库: web_ui 把本轮的 tool_done / notice 收集成 trace_steps, 挂到对应 assistant 消息 (按工具调用 id
分到所属那条, 见 attach_steps), 刷新后前端据此重绘状态 / 用时 / 摘要; 整轮错误存 turn_error。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

from lib.friendly_errors import classify, scrub

SUMMARY_MAX = 300
ARGS_MAX = 600
MAX_STEPS = 400          # 单轮最多落库多少条, 防失控
_PROGRESS_MAX = 50       # 每个 (sid, tool) 队列上限


# ── 摘要 ────────────────────────────────────────────────────────

def _strip_sys(text: str) -> str:
    """去掉给模型看的 [SYSTEM...] 提示 (不给用户看)。"""
    i = text.find("[SYSTEM")
    return text[:i].rstrip() if i > 0 else text


def summarize(result, limit: int = SUMMARY_MAX) -> str:
    s = _strip_sys(result if isinstance(result, str) else str(result or ""))
    s = scrub(s).strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def args_preview(args, limit: int = ARGS_MAX) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
    except Exception:
        s = str(args)
    s = scrub(s)
    return s if len(s) <= limit else s[:limit - 1] + "…"


def is_error_result(result) -> bool:
    return isinstance(result, str) and result.startswith("ERROR")


def tool_done(call_id: str, name: str, result, duration_s: float, retry_of: Optional[str] = None) -> dict:
    """一次工具调用结束 → tool_done 事件体。"""
    ok = not is_error_result(result)
    d = {"id": call_id or "", "name": name or "", "ok": ok,
         "duration_ms": max(0, int(duration_s * 1000)), "summary": summarize(result)}
    if not ok:
        fe = classify(_strip_sys(str(result)), tool=name)
        d["error_kind"] = fe["kind"]
        d["error_title"] = fe["title"]
        d["error_hint"] = fe["hint"]
    if retry_of:
        d["retry_of"] = retry_of
    return d


def call_signature(name: str, args) -> str:
    try:
        return name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)[:2000]
    except Exception:
        return name + ":" + str(args)[:2000]


# ── 进度 / 降级说明 (工具内部调用 report, tool_exec 心跳循环里 drain 并透传) ──

_progress: dict = {}
_plock = threading.Lock()


def report(sid: Optional[str], tool: str, text: str, pct: Optional[float] = None) -> None:
    """工具执行中上报一条进度 / 说明。没有 sid (CLI 直调 / 测试) 时忽略。永不抛异常。"""
    if not sid or not text:
        return
    try:
        item = {"text": str(text)[:200], "ts": time.time()}
        if pct is not None:
            item["pct"] = max(0.0, min(100.0, float(pct)))
        with _plock:
            q = _progress.setdefault((sid, tool), [])
            q.append(item)
            del q[:-_PROGRESS_MAX]
    except Exception:
        pass


def drain(sid: Optional[str], tool: str) -> list:
    if not sid:
        return []
    with _plock:
        return _progress.pop((sid, tool), [])


# ── web_ui 侧: 收集 SSE → 落库 ─────────────────────────────────

def collect_delta(collected: dict, delta: dict) -> None:
    """从一帧 delta 里收集 tool_done / tool_progress / notice / error, 存进 collected。"""
    steps = collected.setdefault("trace_steps", [])
    td = delta.get("tool_done")
    if isinstance(td, dict) and len(steps) < MAX_STEPS:
        step = {k: td[k] for k in ("id", "name", "ok", "duration_ms", "summary", "error_kind",
                                   "error_title", "error_hint", "retry_of") if k in td}
        notes = collected.setdefault("_notes", {}).pop(td.get("id") or "", None)
        if notes:
            step["notes"] = notes[-5:]
        steps.append(step)
    tp = delta.get("tool_progress")
    if isinstance(tp, dict) and tp.get("text"):
        collected.setdefault("_notes", {}).setdefault(tp.get("id") or "", []).append(str(tp["text"])[:200])
    nt = delta.get("notice")
    if isinstance(nt, dict) and nt.get("text") and len(steps) < MAX_STEPS:
        steps.append({"notice": str(nt["text"])[:200], "level": nt.get("level") or "info"})
    er = delta.get("error")
    if isinstance(er, dict) and (er.get("kind") or er.get("reason")):
        collected["turn_error"] = turn_error_from(er)


def turn_error_from(er) -> dict:
    """delta.error (新: 带 kind/title/hint/actions/details) 或 顶层 error 字符串 → 落库用的 turn_error。"""
    if isinstance(er, dict) and er.get("kind"):
        out = {k: er.get(k) for k in ("kind", "title", "hint", "actions", "details", "reason", "content_echo")
               if er.get(k) is not None}
        return out
    raw = er.get("reason") if isinstance(er, dict) else er
    fe = classify(raw)
    if isinstance(er, dict) and er.get("reason"):
        fe["reason"] = er["reason"]
    return fe


def attach_steps(turn_msgs: list, steps: list, turn_error: Optional[dict] = None) -> None:
    """把本轮的 trace_steps 按工具调用 id 分到所属的 assistant 消息 (server 历史重导后一轮有多条);
    找不到归属的 (通知 / id 对不上) 挂到最后一条。turn_error 挂最后一条。"""
    if not turn_msgs:
        return
    owner = {}
    for m in turn_msgs:
        for r in m.get("_tool_results") or []:
            if isinstance(r, dict) and r.get("id"):
                owner[r["id"]] = m
    tail = turn_msgs[-1]
    if steps:
        buckets: dict = {}
        for s in steps:
            m = owner.get(s.get("id")) if s.get("id") else None
            buckets.setdefault(id(m or tail), (m or tail, []))[1].append(s)
        for m, lst in buckets.values():
            if not m.get("trace_steps"):
                m["trace_steps"] = lst
    if turn_error and not tail.get("turn_error"):
        tail["turn_error"] = turn_error


def turn_steps(msgs: list) -> list:
    """一轮里所有 assistant 消息上的 trace_steps 拼起来 (重导时从老缓存取回)。"""
    out = []
    for m in msgs:
        out.extend(m.get("trace_steps") or [])
    return out
