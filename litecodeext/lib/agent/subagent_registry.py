"""lib/agent/subagent_registry.py — 子代理登记表 + 后台子代理 + 主代理控制 (agent_control)。

问题: spawn_agent 原本是阻塞的, 子代理跑完前主代理拿不到控制权, 没法中途补充指令、查看进度或叫停。

做法 (现有主循环只加几处薄调用点):
  登记表  每个子代理 (阻塞模式的也登记, 便于查看) 一条 AgentRecord: id / 所属会话 sid / 角色 / 状态
          (running/done/failed/stopped) / 开始时间 / 当前迭代 / 最近一次工具调用 / 结果。按会话隔离:
          agent_control 只能看到和操作本会话派出的子代理 (别的会话的 id 一律当"找不到")。
          结束的记录保留 agent.subagent_record_ttl 秒 (默认 3600) 后清理; 后台子代理的结果没被取走时
          保留 24 小时 (下一轮还要提示主代理去取)。纯内存, 服务重启即清空。
  后台    spawn_agent(background=true): 单个 task 或 parallel_tasks 每项各起一个后台子代理, 立即返回
          id/角色/任务摘要。每个子代理照旧套三层包装 (操作员锁在 _run_subagent 内; 工作树隔离; 证据),
          结果格式与阻塞模式相同 (开头 [已验证]/[未验证: 原因] + 工作树报告 + 证据小节)。
          后台子代理和主代理同时在跑, 所以 isolation=auto 时写类子代理一律用独立工作树 (workdir 是 git 仓库时)。
          后台子代理不随主回合结束/用户中断主回合而停止, 叫停用 agent_control stop。
          每个会话同时运行的后台子代理数有上限 (agent.max_background_subagents, 默认 4), 超过拒绝派发。
  信箱    agent_control send 投递补充指令 (≤2000 字); 子代理每轮迭代开始前 (tick) 取出, 作为 user 消息
          注入 (前缀 [来自主代理的补充指令]) 并记日志。子代理内部消息不进会话历史, 不经 SSE 推给前端,
          因此不进用户可见内容与记忆提取。准备收尾时信箱里还有指令 → 多跑一轮把它处理掉。
  停止    agent_control stop 置标记, 子代理在下一轮迭代开始时以 "[子代理已中断]" 结束 → 状态 stopped;
          证据判定按非正常结束处理 (未验证), 工作树只提交不合并 (subagent_worktree 对中断前缀的既有逻辑)。
  回合    主回合结束时还有后台子代理在跑 → 不强杀, 在最终回复后追加一条简短备注 (用户可见);
          下一轮开始时本会话有已结束但结果未取走的后台子代理 → turn_context 来源注入一条 [SYSTEM-AGENTS]
          提示 (挂在 user 消息的 turn_context 字段: 界面不显示, 记忆提取前被 strip)。
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

INSTR_PREFIX = "[来自主代理的补充指令]"
AGENTS_PREFIX = "[SYSTEM-AGENTS]"
SEND_MAX = 2000
DEFAULT_MAX_BG = 4
DEFAULT_WAIT = 300
MAX_WAIT = 1800
RECORD_TTL = 3600
UNFETCHED_TTL = 24 * 3600
POLL = 0.2
_FAIL_PREFIXES = ("[子代理超时]", "[子代理异常]", "[子代理错误栈]", "[子代理嵌套过深]", "[子代理已取消",
                  "[子代理已中断]", "(subagent exhausted")
_MULTI_KEYS = ("dag_tasks", "pipeline_tasks", "competitive_tasks", "batch_items")
_STATUS_CN = {"running": "运行中", "done": "已完成", "failed": "失败", "stopped": "已停止"}


@dataclass
class AgentRecord:
    id: str
    sid: str
    agent_type: str
    task: str
    background: bool = False
    status: str = "running"
    started: float = field(default_factory=time.time)
    ended: Optional[float] = None
    iteration: int = 0
    max_iter: int = 0
    last_tool: str = ""
    tool_calls: int = 0
    result: Optional[str] = None
    fetched: bool = False
    inbox: List[str] = field(default_factory=list)
    delivered: int = 0
    stop_requested: bool = False
    stop_reason: str = "主代理请求停止 (agent_control stop)"
    future: Optional[asyncio.Future] = None

    @property
    def brief(self) -> str:
        return " ".join(str(self.task).split())[:40]

    def elapsed(self) -> int:
        return int((self.ended or time.time()) - self.started)


_RECORDS: Dict[str, AgentRecord] = {}
_LOCK = threading.Lock()
_CURRENT: contextvars.ContextVar[Optional[AgentRecord]] = contextvars.ContextVar(
    "subagent_record", default=None)


def _agent_cfg() -> dict:
    try:
        from ..config import CFG
        return CFG.get("agent") or {}
    except Exception:
        return {}


def max_background() -> int:
    try:
        return max(1, int(_agent_cfg().get("max_background_subagents", DEFAULT_MAX_BG)))
    except Exception:
        return DEFAULT_MAX_BG


def _ttl() -> float:
    try:
        return float(_agent_cfg().get("subagent_record_ttl", RECORD_TTL))
    except Exception:
        return float(RECORD_TTL)


def _log():
    from ..config import log
    return log


# ═══════════════════════════ 登记表 ═══════════════════════════

def _gc(now: Optional[float] = None) -> int:
    """清理过期的已结束记录。结果没取走的后台子代理保留更久 (下一轮还要提示)。"""
    now = now or time.time()
    ttl = _ttl()
    n = 0
    with _LOCK:
        for rid, r in list(_RECORDS.items()):
            if r.status == "running" or r.ended is None:
                continue
            keep = UNFETCHED_TTL if (r.background and not r.fetched) else ttl
            if now - r.ended > keep:
                _RECORDS.pop(rid, None)
                n += 1
    return n


def _new_record(sid: Optional[str], agent_type: str, task: str, background: bool) -> AgentRecord:
    rec = AgentRecord(id=f"sa-{uuid.uuid4().hex[:6]}", sid=sid or "", agent_type=agent_type or "coder",
                      task=str(task or ""), background=background)
    with _LOCK:
        _RECORDS[rec.id] = rec
    return rec


def records(sid: Optional[str]) -> List[AgentRecord]:
    _gc()
    with _LOCK:
        return sorted((r for r in _RECORDS.values() if r.sid == (sid or "")), key=lambda r: r.started)


def get(sid: Optional[str], rid: str) -> Optional[AgentRecord]:
    """按 id 取本会话的记录; 别的会话的 id 当作不存在 (会话隔离)。"""
    r = _RECORDS.get(str(rid or "").strip())
    return r if r is not None and r.sid == (sid or "") else None


def running_background(sid: Optional[str]) -> List[AgentRecord]:
    return [r for r in records(sid) if r.background and r.status == "running"]


def _status_of(rec: AgentRecord, out) -> str:
    s = str(out or "").lstrip()
    if rec.stop_requested and s.startswith("[子代理已中断]"):
        return "stopped"
    return "failed" if s.startswith(_FAIL_PREFIXES) else "done"


def _finish(rec: AgentRecord, status: str, result: str) -> None:
    rec.status, rec.result, rec.ended = status, result, time.time()
    rec.inbox.clear()


# ═══════════════════════════ 子代理侧钩子 (subagent._run_subagent_core) ═══════════════════════════

def current() -> Optional[AgentRecord]:
    return _CURRENT.get()


def is_background() -> bool:
    r = _CURRENT.get()
    return bool(r and r.background)


def steered() -> bool:
    """收到过补充指令的子代理: 结果受额外指令影响, 不进 (agent_type, task) 结果缓存。"""
    r = _CURRENT.get()
    return bool(r and r.delivered)


def tick(messages: list, it: int, max_iter: int, agent_type: str) -> Optional[str]:
    """每轮迭代开始前调用: 记进度; 有停止请求 → 返回中断文本 (调用方据此结束); 有补充指令 → 注入。"""
    rec = _CURRENT.get()
    if rec is None:
        return None
    rec.iteration, rec.max_iter = it + 1, max_iter
    if rec.stop_requested:
        _log().warning(f"  [subagent:{agent_type}] {rec.id} 主代理请求停止, 于 iter={it + 1} 结束")
        return (f"[子代理已中断] {rec.stop_reason}, 子代理 {agent_type} ({rec.id}) "
                f"于 iter={it + 1} 停止; 已调用工具 {rec.tool_calls} 次, 最近: {rec.last_tool or '无'}。")
    while rec.inbox:
        text = rec.inbox.pop(0)
        msg = f"{INSTR_PREFIX} {text}"
        if messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), str):
            messages[-1] = dict(messages[-1], content=messages[-1]["content"] + "\n\n" + msg)
        else:
            messages.append({"role": "user", "content": msg})
        rec.delivered += 1
        _log().info(f"  [subagent:{agent_type}] {rec.id} 收到主代理补充指令 @iter={it + 1}: {text[:120]}")
    return None


def stop_session(sid: Optional[str], reason: str = "用户在界面点了停止") -> int:
    """用户中断会话时一并停止该会话所有运行中的后台子代理 (下一轮迭代开始时结束)。返回停止的个数。
    后台子代理没有前端面板, 不随"停止"一起停的话用户无从叫停, 跑偏了只能干等占着模型。"""
    n = 0
    for rec in running_background(sid):
        if not rec.stop_requested:
            rec.stop_requested, rec.stop_reason = True, reason
            n += 1
    if n:
        _log().warning(f"  [subagent-registry] 会话 {sid} 中断, 一并停止 {n} 个后台子代理")
    return n


def pending() -> bool:
    """子代理准备收尾时信箱里还有补充指令 (调用方应再跑一轮把它处理掉)。"""
    r = _CURRENT.get()
    return bool(r and r.inbox and not r.stop_requested)


def note_tool(fn_name: str, fn_args) -> None:
    r = _CURRENT.get()
    if r is None:
        return
    r.tool_calls += 1
    a = fn_args if isinstance(fn_args, dict) else {}
    key = (a.get("filepath") or a.get("path") or a.get("command") or a.get("query") or a.get("pattern") or a.get("url")
           or a.get("action") or "")
    r.last_tool = f"{fn_name}({' '.join(str(key).split())[:60]})" if key else fn_name


# ═══════════════════════════ 派发侧 ═══════════════════════════

def track(run_fn: Callable, sid: Optional[str]) -> Callable:
    """阻塞模式: 登记每个子代理 (结果原样返回, 行为不变), 记录视为已取走。"""
    async def _tracked(task, agent_type="coder", context="", sse_emit=None, *a, **kw):
        rec = _new_record(sid, agent_type, task, background=False)
        rec.fetched = True
        token = _CURRENT.set(rec)
        try:
            out = await run_fn(task, agent_type, context, sse_emit, *a, **kw)
        except asyncio.CancelledError:
            _finish(rec, "stopped", "[子代理已取消: 父会话被中断]")
            raise
        except BaseException as e:
            _finish(rec, "failed", f"[子代理异常] {e}")
            raise
        finally:
            _CURRENT.reset(token)
        _finish(rec, _status_of(rec, out), out if isinstance(out, str) else str(out))
        return out
    return _tracked


def default_timeout(agent_type: str) -> int:
    """与 spawn_dispatch 单代理模式一致的超时。"""
    from .subagent_operator import is_operator, DEFAULT_TIMEOUT
    if is_operator(agent_type):
        return DEFAULT_TIMEOUT
    return {"writer": 900, "researcher": 400, "critic": 180}.get(agent_type, 600)


def _items(args: dict):
    if any(args.get(k) for k in _MULTI_KEYS):
        return None
    par = args.get("parallel_tasks")
    if isinstance(par, list) and par:
        return [t if isinstance(t, dict) else {"task": str(t)} for t in par]
    return [{"task": args.get("task", ""), "agent_type": args.get("agent_type", "coder"),
             "context": args.get("context", "")}]


async def launch_background(args: dict, sid: Optional[str], run_fn: Callable) -> str:
    """spawn_agent(background=true): 起后台子代理, 立即返回 id 列表。run_fn 签名同 _run_subagent_safe。

    args 须已经过 subagent_operator.prepare_spawn_args (operator 的 alias 标记已写进 task)。
    """
    items = _items(args)
    if items is None:
        return ("ERROR: background=true 只支持单个 task 或 parallel_tasks (互相独立的任务); "
                "dag_tasks / pipeline_tasks / competitive_tasks / batch_items 有先后依赖或汇总, "
                "请去掉 background 按阻塞方式运行, 或拆成独立任务后台派发。")
    bad = [i for i, t in enumerate(items) if not str(t.get("task") or "").strip()]
    if bad:
        return f"ERROR: background 派发的任务缺少 task 文本 (第 {', '.join(str(i + 1) for i in bad)} 项)"
    running = running_background(sid)
    cap = max_background()
    if len(running) + len(items) > cap:
        return (f"ERROR: 拒绝派发 —— 本会话同时运行的后台子代理上限为 {cap} 个, 现在已有 {len(running)} 个在跑"
                f" ({', '.join(f'{r.id} {r.agent_type}' for r in running) or '无'}), 本次又要派 {len(items)} 个。"
                "请先用 agent_control wait/result 收掉已有的, 或 stop 不需要的, 或改用阻塞方式 (去掉 background)。")
    from .subagent_worktree import plan_isolation, is_write_role
    from .subagent_evidence import plan_evidence
    iso_mode = str(args.get("isolation") or "auto").strip().lower()
    lines = []
    for t in items:
        agent_type = t.get("agent_type") or args.get("agent_type") or "coder"
        task = str(t.get("task") or "")
        context = t.get("context", args.get("context", "")) or ""
        rec = _new_record(sid, agent_type, task, background=True)
        # 后台子代理与主代理/其他后台子代理同时改文件 → auto 时写类一律隔离 (workdir 是 git 仓库时)
        iso_args = {"workdir": t.get("workdir") or args.get("workdir"),
                    "isolation": "worktree" if iso_mode == "auto" else iso_mode}
        iso = plan_isolation(iso_args, single=True) if is_write_role(agent_type) else plan_isolation(
            {"isolation": "none"}, single=True)
        ev = plan_evidence()
        timeout = int(t.get("timeout") or default_timeout(agent_type))
        rec.future = asyncio.ensure_future(_bg_job(rec, run_fn, iso, ev, task, agent_type, context, timeout, sid))
        lines.append(f"- {rec.id}  {agent_type}「{rec.brief}」")
    _log().info(f"  [agent-control] sid={(sid or '')[:8]} 后台派出 {len(items)} 个子代理: "
                + ", ".join(ln.split()[1] for ln in lines))
    return (f"已在后台派出 {len(items)} 个子代理 (不阻塞, 你现在可以继续做别的):\n" + "\n".join(lines) + "\n"
            "后续: agent_control(action=\"status\"/\"list\") 看进度; action=\"send\" 补充指令 (子代理下一轮迭代前收到); "
            "action=\"wait\" 等结束并取结果 (默认最多等 300s, 超时返回当前进度); action=\"stop\" 叫停。"
            "结果开头带 [已验证]/[未验证: 原因], 未验证的结论只能当线索。")


async def _bg_job(rec, run_fn, iso, ev, task, agent_type, context, timeout, sid) -> None:
    token = _CURRENT.set(rec)
    try:
        out = await ev.wrap(iso.wrap(run_fn))(task, agent_type, context, None,
                                             timeout=timeout, parent_sid=sid)
        out = out if isinstance(out, str) else str(out)
        status = _status_of(rec, out)
        if iso.summary():
            out += "\n" + iso.summary()
        _finish(rec, status, ev.decorate(out))
    except asyncio.CancelledError:   # 事件循环关闭 / 进程退出
        _finish(rec, "stopped", ev.decorate("[子代理已取消: 后台任务被取消 (服务关闭或事件循环退出)]"))
        raise
    except Exception as e:
        _finish(rec, "failed", ev.decorate(f"[子代理异常] {agent_type} 后台运行出错: {e}"))
    finally:
        _CURRENT.reset(token)
        _log().info(f"  [agent-control] {rec.id} {agent_type} 结束: {rec.status} ({rec.elapsed()}s)")


# ═══════════════════════════ agent_control 工具 ═══════════════════════════

def _progress(r: AgentRecord) -> str:
    it = f"{r.iteration}/{r.max_iter}" if r.max_iter else str(r.iteration)
    s = (f"{r.id} {r.agent_type}「{r.brief}」{_STATUS_CN.get(r.status, r.status)} "
         f"{'后台' if r.background else '阻塞'} 已运行 {r.elapsed()}s 迭代 {it} "
         f"工具调用 {r.tool_calls} 次 最近: {r.last_tool or '无'}")
    if r.status == "running":
        if r.inbox:
            s += f" 信箱待送 {len(r.inbox)} 条"
        if r.stop_requested:
            s += " (已请求停止)"
    elif r.background:
        s += " 结果已取" if r.fetched else " 结果未取"
    if r.delivered:
        s += f" 已送达补充指令 {r.delivered} 条"
    return s


def _take(r: AgentRecord) -> str:
    r.fetched = True
    return f"=== {r.id} {r.agent_type}「{r.brief}」{_STATUS_CN.get(r.status, r.status)} ({r.elapsed()}s) ===\n{r.result or ''}"


def _not_found(rid: str) -> str:
    return f"ERROR: 找不到子代理 id={rid!r} (本会话没有派出过它, 或记录已过期)。用 agent_control(action=\"list\") 查看。"


def _interrupted(sid: Optional[str]) -> bool:
    if not sid:
        return False
    try:
        from ..session import check_interrupt
        return bool(check_interrupt(sid))
    except Exception:
        return False


async def agent_control_tool(sid: Optional[str], args: dict) -> str:
    args = args or {}
    action = str(args.get("action") or "list").strip().lower()
    rid = str(args.get("id") or "").strip()
    if action == "list":
        rs = records(sid)
        if not rs:
            return "本会话没有子代理记录。"
        run = sum(1 for r in rs if r.status == "running" and r.background)
        return (f"本会话子代理 {len(rs)} 个 (后台运行中 {run}/{max_background()}):\n"
                + "\n".join("- " + _progress(r) for r in rs))
    if action not in ("status", "send", "wait", "result", "stop"):
        return f"ERROR: 未知 action={action!r}, 可选 list / status / send / wait / result / stop"
    if action == "wait":
        return await _wait(sid, rid, args.get("timeout"))
    if not rid:
        return f"ERROR: action={action} 需要 id (spawn_agent 后台派发返回的 sa-xxxxxx)"
    r = get(sid, rid)
    if r is None:
        return _not_found(rid)
    if action == "status":
        return _progress(r)
    if action == "result":
        if r.status == "running":
            return f"子代理还在运行, 暂无结果。当前进度: {_progress(r)}"
        return _take(r)
    if action == "stop":
        if r.status != "running":
            return f"子代理 {r.id} 已结束 (状态 {r.status}), 无需停止。"
        r.stop_requested = True
        _log().info(f"  [agent-control] {r.id} 请求停止")
        return (f"已请求停止 {r.id}: 它会在下一轮迭代开始时结束 (状态 stopped, 证据按非正常结束判定; "
                "若在独立工作树里, 改动只提交不合并)。正在执行的工具/模型调用会先跑完。用 wait 拿最终结果。")
    # send
    msg = str(args.get("message") or "").strip()
    if not msg:
        return "ERROR: send 需要 message (补充指令文本)"
    if len(msg) > SEND_MAX:
        return f"ERROR: 补充指令过长 ({len(msg)} 字), 上限 {SEND_MAX} 字"
    if r.status != "running":
        return (f"ERROR: 子代理 {r.id} 已结束 (状态 {r.status}), 补充指令无法送达。"
                "可用 result 取结果, 需要的话带上新要求重新 spawn_agent。")
    if r.stop_requested:
        return f"ERROR: 子代理 {r.id} 已被请求停止, 不再接收补充指令。"
    r.inbox.append(msg)
    _log().info(f"  [agent-control] {r.id} 投递补充指令 ({len(msg)} 字): {msg[:80]}")
    return (f"已投递给 {r.id} ({r.agent_type}): 它会在下一轮迭代开始前收到 (当前迭代 {r.iteration}, "
            f"信箱待送 {len(r.inbox)} 条)。正在执行的工具/模型调用不会被打断。")


async def _wait(sid: Optional[str], rid: str, timeout) -> str:
    try:
        t = float(timeout) if timeout is not None else float(DEFAULT_WAIT)
    except Exception:
        t = float(DEFAULT_WAIT)
    t = min(max(t, 0.0), float(MAX_WAIT))
    if rid:
        r = get(sid, rid)
        if r is None:
            return _not_found(rid)
        targets = [r]
    else:   # 全部: 本会话还在跑的 + 已结束但结果没取走的后台子代理
        targets = [r for r in records(sid) if r.status == "running" or (r.background and not r.fetched)]
        if not targets:
            return "本会话没有在运行或待取结果的子代理。"
    deadline = time.time() + t
    interrupted = False
    while any(r.status == "running" for r in targets) and time.time() < deadline:
        if _interrupted(sid):
            interrupted = True
            break
        await asyncio.sleep(min(POLL, max(0.01, deadline - time.time())))
    done = [r for r in targets if r.status != "running"]
    left = [r for r in targets if r.status == "running"]
    parts = [_take(r) for r in done]
    if left:
        why = "主会话被用户中断, 停止等待" if interrupted else f"等待 {t:g}s 超时"
        parts.append(f"[{why}] 仍在运行 {len(left)} 个 (不是错误, 子代理继续在后台跑; 可稍后再 wait, "
                     "或先做别的、send 补充指令、stop 叫停):\n" + "\n".join("- " + _progress(r) for r in left))
    return "\n\n".join(parts)


# ═══════════════════════════ 回合边界 ═══════════════════════════

def turn_end_note(sid: Optional[str], new_msgs: Optional[list] = None) -> str:
    """主回合收尾: 还有后台子代理在跑 → 返回一条简短备注 (用户可见), 并追加到最终回复后。"""
    if not sid:
        return ""
    try:
        run = running_background(sid)
    except Exception:
        return ""
    if not run:
        return ""
    names = "、".join(f"{r.id} {r.agent_type}「{r.brief[:20]}」" for r in run[:4]) + (" 等" if len(run) > 4 else "")
    note = (f"（系统备注: 还有 {len(run)} 个后台子代理在运行: {names}; 结束后结果会保存, "
            "下一轮可用 agent_control 查看。）")
    if new_msgs is not None:
        last = new_msgs[-1] if new_msgs else None
        if (isinstance(last, dict) and last.get("role") == "assistant" and isinstance(last.get("content"), str)
                and last.get("content") and not last.get("tool_calls")):
            last["content"] = last["content"].rstrip() + "\n\n" + note
        else:
            new_msgs.append({"role": "assistant", "content": note})
    return "\n\n" + note


def pending_notice(sid: Optional[str]) -> str:
    """下一轮开始: 本会话有已结束但结果未取走的后台子代理 → 一条 [SYSTEM-AGENTS] 简短提示。"""
    if not sid:
        return ""
    rs = [r for r in records(sid) if r.background and r.status != "running" and not r.fetched]
    if not rs:
        return ""
    items = "; ".join(f"{r.id} ({r.agent_type}, {_STATUS_CN.get(r.status, r.status)}, 「{r.brief[:24]}」)"
                      for r in rs[:6]) + (" 等" if len(rs) > 6 else "")
    run = len(running_background(sid))
    return (f"{AGENTS_PREFIX} 本会话有 {len(rs)} 个后台子代理已结束, 结果尚未取走: {items}。"
            + (f"另有 {run} 个仍在运行。" if run else "")
            + "需要时用 agent_control(action=\"result\", id=...) 取结果 (未验证的结论只能当线索)。")


def turn_source(inp) -> str:
    """lib/turn_context 来源: 同一条提示本会话历史里已给过就不重复。"""
    text = pending_notice(getattr(inp, "session_id", "") or None)
    if not text:
        return ""
    try:
        from .. import turn_context as _tc
        if text in _tc.seen_in(getattr(inp, "history", []) or []):
            return ""
    except Exception:
        pass
    return text
