"""lib/agent/subagent_events.py — 每个子代理的完整过程记录 (Web 任务树 / 只读视图 / 回看用)。

问题: 子代理在登记表里只有"最近一条进度", 界面上是黑盒, 用户看不到它每一步在干什么。
做法: 登记表每条 AgentRecord 带一个事件环形缓冲 (最近 EVENT_CAP 条, 序号 seq 单调递增), 子代理侧的
薄钩子 (subagent_registry 的 tick / tool_begin / note_tool / observe / _finish) 往里追加事件,
同时逐条追加落盘到 <WORKSPACE>/telemetry/subagent_events/<sid>/<id>.jsonl (结束时压缩成最近 EVENT_CAP 条),
服务重启后从磁盘读回 (前台 / 后台子代理都记)。

事件 (每条都有 seq / t / kind; 文本全部经 tool_trace 的摘要 + 脱敏):
  start        {agent_type, task, background, parent_id}
  tool_start   {id, name, args, iteration}                      args = 脱敏后的参数 JSON 摘要
  tool_done    {id, name, ok, duration_ms, summary, error_*}    同 tool_trace.tool_done (主时间线同一格式)
  thinking     {text}                                           本轮思考摘要
  text         {text}                                           本轮正文片段 (步骤间旁白)
  instruction  {text, state: queued|delivered}                  补充指令
  notice       {text, level}                                    停止请求 / 工作树收尾等说明
  worktree     {id, branch, status, status_cn, files}           工作树挂上 / 收尾 / 合并 / 丢弃
  end          {status, status_cn, result, elapsed}             最终结果 (摘要上限 RESULT_MAX)

增量拉取: since(rec, after) 返回 seq > after 的事件; 环形缓冲被挤掉的老事件不再返回 (返回体带 first_seq 供前端判断)。
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

EVENT_CAP = 300
TEXT_MAX = 600
THINK_MAX = 300
RESULT_MAX = 4000
FILE_KEEP_SECS = 7 * 24 * 3600          # 事件文件保留 7 天 (内存记录按登记表 TTL 淘汰)
EVENTS_SUBDIR = ("telemetry", "subagent_events")
_FLOCK = threading.Lock()
_THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)

try:
    from plugins.tools.guard import _redact as _redact_args   # 与封禁提示同一套敏感键规则
except Exception:  # pragma: no cover — plugins 不在 sys.path 时退化为只做文本脱敏
    def _redact_args(v, depth: int = 0):
        return v


def new_buffer() -> deque:
    return deque(maxlen=EVENT_CAP)


def _root() -> Path:
    from .subagent_registry import _workspace
    return _workspace().joinpath(*EVENTS_SUBDIR)


def _file(rec) -> Path:
    from .subagent_registry import _safe_name
    return _root() / _safe_name(rec.sid) / f"{_safe_name(rec.id)}.jsonl"


def _clip(text, limit: int) -> str:
    from ..tool_trace import summarize
    return summarize(text, limit)


def emit(rec, kind: str, **fields) -> Optional[dict]:
    """给子代理记录追加一条事件 (内存环形缓冲 + 追加落盘)。永不抛异常。"""
    if rec is None:
        return None
    try:
        rec.seq += 1
        ev = {"seq": rec.seq, "t": round(time.time(), 3), "kind": kind}
        ev.update({k: v for k, v in fields.items() if v is not None})
        rec.events.append(ev)
        _append(rec, ev)
        return ev
    except Exception:
        return None


def _append(rec, ev: dict) -> None:
    try:
        p = _file(rec)
        with _FLOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


def compact(rec) -> None:
    """子代理结束: 事件文件压缩成内存里最近 EVENT_CAP 条 (原子写)。"""
    try:
        from ..atomic_io import atomic_write_text
        evs = list(rec.events)
        if not evs:
            return
        with _FLOCK:
            atomic_write_text(_file(rec), "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs))
    except Exception:
        pass


def load(rec) -> None:
    """重启后恢复的记录: 首次查看时从磁盘读回事件 (只读一次)。"""
    if rec.events or getattr(rec, "_events_loaded", False):
        return
    rec._events_loaded = True
    try:
        lines = _file(rec).read_text("utf-8").splitlines()[-EVENT_CAP:]
    except Exception:
        return
    for ln in lines:
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("seq"), int):
            rec.events.append(ev)
    if rec.events:
        rec.seq = max(rec.seq, rec.events[-1]["seq"])


def since(rec, after: int = 0, limit: int = EVENT_CAP) -> dict:
    load(rec)
    evs = [e for e in list(rec.events) if e.get("seq", 0) > after][:max(1, limit)]
    first = rec.events[0]["seq"] if rec.events else 0
    return {"items": evs, "last_seq": rec.seq, "first_seq": first,
            "more": bool(evs) and evs[-1]["seq"] < rec.seq}


def remove_file(rec) -> None:
    try:
        _file(rec).unlink()
    except Exception:
        pass


def cleanup(now: Optional[float] = None) -> int:
    """删掉超过 FILE_KEEP_SECS 没更新的事件文件。"""
    now = now or time.time()
    n = 0
    root = _root()
    if not root.is_dir():
        return 0
    for p in root.glob("*/*.jsonl"):
        try:
            if now - p.stat().st_mtime > FILE_KEEP_SECS:
                p.unlink()
                n += 1
        except Exception:
            pass
    return n


# ═══════════════════════════ 具体事件 (subagent_registry 的钩子调用) ═══════════════════════════

def args_text(fn_args) -> str:
    from ..tool_trace import args_preview
    from ..scrub import scrub_secrets
    try:
        a = _redact_args(fn_args) if isinstance(fn_args, (dict, list)) else fn_args
    except Exception:
        a = fn_args
    return args_preview(scrub_secrets(json.dumps(a, ensure_ascii=False)) if not isinstance(a, str) else scrub_secrets(a))


def tool_start(rec, call_id: str, fn_name: str, fn_args) -> None:
    rec.cur_call = (call_id or f"c{rec.seq + 1}", time.time())
    emit(rec, "tool_start", id=rec.cur_call[0], name=fn_name, args=args_text(fn_args), iteration=rec.iteration)


def tool_done(rec, call_id: str, fn_name: str, result) -> None:
    from ..tool_trace import tool_done as _td
    cid, t0 = rec.cur_call if rec.cur_call else (call_id, time.time())
    rec.cur_call = None
    d = _td(call_id or cid, fn_name, result if isinstance(result, str) else str(result), time.time() - t0)
    d.pop("id", None)
    emit(rec, "tool_done", id=call_id or cid, **d)


def stream_done(rec, content: str, reasoning: str) -> None:
    """一轮模型输出结束: 思考摘要 + 正文片段 (正文里内联的 <think> 也算思考)。"""
    content = content or ""
    think = (reasoning or "").strip()
    m = _THINK_RE.search(content)
    if m:
        think = (think + "\n" + m.group(1).strip()).strip()
        content = _THINK_RE.sub("", content)
    if think:
        emit(rec, "thinking", text=_clip(" ".join(think.split()), THINK_MAX))
    content = content.strip()
    if content:
        emit(rec, "text", text=_clip(content, TEXT_MAX))


async def observe(rec, stream):
    """包住子代理的模型流: 原样转发每个 chunk, 流结束时记思考摘要 + 正文片段。"""
    content, reasoning = [], []
    try:
        async for chunk in stream:
            try:
                ch = (chunk.get("choices") or [{}])[0].get("delta") or {}
                if ch.get("content"):
                    content.append(ch["content"])
                r = ch.get("reasoning_content") or ch.get("reasoning")
                if isinstance(r, str) and r:
                    reasoning.append(r)
            except Exception:
                pass
            yield chunk
    finally:
        if rec is not None:
            stream_done(rec, "".join(content), "".join(reasoning))


def end(rec) -> None:
    from .subagent_registry import _STATUS_CN
    emit(rec, "end", status=rec.status, status_cn=_STATUS_CN.get(rec.status, rec.status),
         result=_clip(rec.result or "", RESULT_MAX), elapsed=rec.elapsed())
    compact(rec)


# ═══════════════════════════ 工作树 (分支操作) ═══════════════════════════

def link_worktree(rec) -> None:
    """tick 时: 子代理挂了独立工作树 → 记下工作树 id (只记一次)。"""
    if rec.worktree_id:
        return
    try:
        from .subagent_worktree import current
        ctx = current()
    except Exception:
        ctx = None
    if ctx is None:
        return
    rec.worktree_id = ctx.wid
    emit(rec, "worktree", **worktree_info(rec))


def worktree_info(rec) -> Optional[dict]:
    if not getattr(rec, "worktree_id", ""):
        return None
    from .subagent_worktree import load_meta, _STATUS_CN as _WT_CN
    m = load_meta(rec.worktree_id) or {"id": rec.worktree_id, "status": "missing"}
    st = m.get("status") or "?"
    n_files = len(m.get("files") or [])
    if st == "running" and m.get("path"):      # 运行中还没统计: 看工作树里的改动 (含未跟踪, 不含缓存)
        try:
            from .subagent_worktree import _git, is_junk
            out = _git(["status", "--porcelain"], m["path"], timeout=10).stdout
            n_files = sum(1 for ln in out.splitlines() if ln.strip() and not is_junk(ln[3:]))
        except Exception:
            pass
    return {"id": m.get("id") or rec.worktree_id, "branch": m.get("branch") or "", "status": st,
            "status_cn": _WT_CN.get(st, "记录丢失" if st == "missing" else st),
            "files": n_files, "base_branch": m.get("base_branch") or "",
            "repo": m.get("repo") or "", "conflicts": m.get("conflicts") or [],
            "note": (m.get("note") or "")[:300]}
