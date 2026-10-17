"""主代理给运行中的子代理补充指令 (lib/agent/subagent_registry.py + agent_control) 的回归用例。

经真实 spawn_agent 派发路径 (主循环 spawn_dispatch / handler) → 三层包装 → _run_subagent 跑,
只把子代理 LLM 流 (_vllm_stream) 和 execute_tool 打桩: 假 LLM 按任务名调用一个 async 行为函数,
行为函数能看到子代理当前的全部消息 (据此验证补充指令确实进了子代理上下文、并改变了行为)。
"""
import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.agent import subagent as sa  # noqa: E402
from lib.agent import subagent_registry as reg  # noqa: E402
from lib.agent import subagent_worktree as wt  # noqa: E402
from lib.config import CFG, log as _log  # noqa: E402

PYTEST_OK = "tests/test_a.py ...\n3 passed in 0.05s"


def _git(repo, *args):
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=str(repo),
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(wt, "WORKSPACE_OVERRIDE", ws)
    monkeypatch.setitem(CFG, "agent", {**(CFG.get("agent") or {}), "subagent_evidence": True})
    sa._SUBAGENT_CACHE.clear()
    reg._RECORDS.clear()
    behaviors, seen, calls, logs = {}, {}, [], []

    async def fake_stream(messages, tools):
        rec = reg.current()          # 滚动摘要会替换掉任务消息, 以登记表里的任务为准
        task = (rec.task if rec else str(messages[1]["content"])).split("\n\n")[0]
        hist = seen.setdefault(task, [])
        hist.append([dict(m) for m in messages])
        fn = behaviors.get(task)
        step = await fn(messages, len(hist) - 1) if fn else ("text", f"done: {task}")
        if step[0] == "text":
            yield {"choices": [{"delta": {"content": step[1]}}]}
            return
        _, name, args = step
        yield {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": f"c{len(calls)}-{time.time_ns()}",
            "function": {"name": name, "arguments": json.dumps(args)}}]}}]}

    async def fake_exec(name, args, sid):
        calls.append((name, dict(args)))
        if name == "write_file":
            p = Path(args["filepath"])
            p = p if p.is_absolute() else ws / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"wrote {p}", None
        if name == "execute_shell":
            if args["command"].split("&& ")[-1].strip() == "pytest -q":
                return PYTEST_OK, None
            r = subprocess.run(args["command"], shell=True, capture_output=True, text=True, cwd=str(ws))
            return (r.stdout + r.stderr).strip() or "(exit 0)", None
        if name == "search_code":
            return f"a.py:1: {args.get('pattern')} = 1", None
        return f"fake {name}", None

    class _H(logging.Handler):
        def emit(self, rec):
            logs.append(rec.getMessage())
    h = _H()
    _log.addHandler(h)
    monkeypatch.setattr(sa, "_vllm_stream", fake_stream)
    monkeypatch.setattr(sa, "execute_tool", fake_exec)
    yield SimpleNamespace(ws=ws, behaviors=behaviors, seen=seen, calls=calls, logs=logs)
    _log.removeHandler(h)
    reg._RECORDS.clear()


async def _dispatch(args, sid):
    """生产主循环路径: spawn_dispatch._dispatch_spawn_agent。"""
    from lib.agent import spawn_dispatch as sd
    out = {"effective_timeout": 0}
    async for _ in sd._dispatch_spawn_agent(args, SimpleNamespace(session_id=sid, tracer=None),
                                            None, asyncio.Queue(), out):
        pass
    return out["result"]


async def _ctl(sid, **args):
    from core.tools.handlers.agent import h_agent_control
    out, _ = await h_agent_control(sid, args)
    return out


def _ids(text):
    import re
    return re.findall(r"\b(sa-[0-9a-f]{6})\b", text)


async def _until(pred, timeout=5.0):
    t0 = time.time()
    while not pred():
        if time.time() - t0 > timeout:
            raise AssertionError("条件等待超时")
        await asyncio.sleep(0.01)


def _search_forever(tag):
    """只读角色行为: 一直换参数搜代码 (不触发循环检测); 看到补充指令就按指令收尾。"""
    async def fn(messages, i):
        instr = [m["content"] for m in messages if reg.INSTR_PREFIX in str(m.get("content", ""))]
        if instr:
            return ("text", f"{tag}: 已按补充指令改用 B 方案, 结论 OK")
        await asyncio.sleep(0.05)
        return ("tool", "search_code", {"pattern": f"{tag}{i}"})
    return fn


# ── 后台派发立即返回; send 的指令下一轮出现在子代理消息里并改变行为; wait 拿到带证据标记的结果 ──
def test_background_send_wait(env):
    t = "后台探索 A"
    env.behaviors[t] = _search_forever("A")

    async def go():
        t0 = time.time()
        res = await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-A")
        assert time.time() - t0 < 0.5, "后台派发应立即返回"
        assert res.startswith("已在后台派出 1 个子代理"), res
        (rid,) = _ids(res)
        assert f"{rid}  explorer「{t}」" in res
        rec = reg.get("sid-A", rid)
        assert rec.status == "running" and rec.background
        # 主会话被中断不影响后台子代理 (后台子代理不随主回合中断而停)
        from lib.session import _interrupt_flags, _interrupt_lock
        with _interrupt_lock:
            _interrupt_flags.add("sid-A")
        await _until(lambda: rec.iteration >= 3)
        with _interrupt_lock:
            _interrupt_flags.discard("sid-A")
        assert rec.status == "running"
        st = await _ctl("sid-A", action="status", id=rid)
        assert "运行中" in st and "search_code(" in st, st
        r = await _ctl("sid-A", action="send", id=rid, message="不要再搜了, 改用 B 方案")
        assert r.startswith(f"已投递给 {rid}"), r
        w = await _ctl("sid-A", action="wait", id=rid, timeout=10)
        return rid, w
    rid, w = asyncio.run(go())
    assert w.startswith(f"=== {rid} explorer「{t}」已完成"), w
    body = w.split("\n", 1)[1]
    assert body.startswith("[已验证]\nA: 已按补充指令改用 B 方案, 结论 OK"), body
    assert "【证据】" in body and "search_code×" in body
    assert reg.INSTR_PREFIX not in body                       # 注入文案不进交付正文
    # 指令作为 user 消息出现在子代理随后那一轮的上下文里
    last = env.seen[t][-1]
    injected = [m for m in last if m["role"] == "user" and str(m["content"]).startswith(reg.INSTR_PREFIX)]
    assert len(injected) == 1 and "改用 B 方案" in injected[0]["content"]
    assert not any(reg.INSTR_PREFIX in str(m.get("content")) for m in env.seen[t][-2])   # 上一轮还没有
    assert any("收到主代理补充指令" in x and rid in x for x in env.logs)               # 日志有记录
    rec = reg.get("sid-A", rid)
    assert rec.status == "done" and rec.fetched and rec.delivered == 1
    # 已结束 → send 明确报错; 过长指令也报错
    r2 = asyncio.run(_ctl("sid-A", action="send", id=rid, message="再补一句"))
    assert r2.startswith("ERROR") and "已结束" in r2, r2
    assert "上限 2000" in asyncio.run(_ctl("sid-A", action="send", id=rid, message="x" * 2001))


# ── 子代理收尾那一轮里到达的指令不会丢: 信箱非空 → 多跑一轮处理 ──
def test_instruction_arriving_during_final_round(env):
    t = "收尾时补充"

    async def fn(messages, i):
        if i == 0:
            env.started.set()
            await env.go.wait()
            return ("text", "初版结论")
        assert reg.INSTR_PREFIX in str(messages[-1]["content"])
        return ("text", "修订结论: 已补上边界情况")
    env.behaviors[t] = fn

    async def go():
        env.started, env.go = asyncio.Event(), asyncio.Event()
        (rid,) = _ids(await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-F"))
        await env.started.wait()
        assert (await _ctl("sid-F", action="send", id=rid, message="别忘了边界情况")).startswith("已投递")
        env.go.set()
        return await _ctl("sid-F", action="wait", id=rid, timeout=5)
    w = asyncio.run(go())
    assert "修订结论: 已补上边界情况" in w and "初版结论" not in w, w
    assert len(env.seen[t]) == 2


# ── wait 超时返回进度而不是报错; result 未结束返回进度; stop 后状态 stopped、证据按非正常结束 ──
def test_wait_timeout_progress_then_stop(env):
    t = "慢任务"
    env.behaviors[t] = _search_forever("S")

    async def go():
        (rid,) = _ids(await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-W"))
        t0 = time.time()
        w = await _ctl("sid-W", action="wait", id=rid, timeout=0.3)
        assert 0.25 < time.time() - t0 < 2
        r = await _ctl("sid-W", action="result", id=rid)
        s = await _ctl("sid-W", action="stop", id=rid)
        f = await _ctl("sid-W", action="wait", id=rid, timeout=5)
        return rid, w, r, s, f
    rid, w, r, s, f = asyncio.run(go())
    assert not w.startswith("ERROR") and "[等待 0.3s 超时] 仍在运行 1 个" in w and rid in w and "迭代" in w, w
    assert r.startswith("子代理还在运行, 暂无结果"), r
    assert s.startswith(f"已请求停止 {rid}"), s
    assert f.startswith(f"=== {rid} explorer「{t}」已停止"), f
    assert "[未验证: 子代理未正常结束 [子代理已中断]" in f and "agent_control stop" in f, f
    assert reg.get("sid-W", rid).status == "stopped"
    assert "已结束" in asyncio.run(_ctl("sid-W", action="stop", id=rid))


# ── stop 在工作树里的写类后台子代理: 改动只提交不合并, 主工作区不变 ──
def test_stop_worktree_commit_not_merge(env):
    repo = env.ws / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    t = "后台改代码"

    async def fn(messages, i):
        if i == 0:
            return ("tool", "write_file", {"filepath": "a.py", "content": "x = 'bg'\n"})
        await asyncio.sleep(0.03)
        return ("tool", "execute_shell", {"command": f"echo step {i}"})
    env.behaviors[t] = fn

    async def go():
        res = await _dispatch({"task": t, "agent_type": "coder", "background": True, "workdir": str(repo)}, "sid-T")
        (rid,) = _ids(res)
        rec = reg.get("sid-T", rid)
        await _until(lambda: rec.iteration >= 3)
        await _ctl("sid-T", action="stop", id=rid)
        return rid, await _ctl("sid-T", action="wait", id=rid, timeout=10)
    rid, w = asyncio.run(go())
    assert reg.get("sid-T", rid).status == "stopped"
    assert "[未验证: 子代理未正常结束 [子代理已中断]" in w, w
    assert "] 已提交未合并" in w, w                                      # 工作树: 只提交不合并
    assert (repo / "a.py").read_text() == "x = 1\n"                       # 主工作区未被改
    br = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/lc-agent/").split()
    assert len(br) == 1 and _git(repo, "show", f"{br[0]}:a.py") == "x = 'bg'"


# ── 会话隔离: B 会话看不到也操作不了 A 的子代理 ──
def test_session_isolation(env):
    t = "A 的任务"
    env.behaviors[t] = _search_forever("I")

    async def go():
        (rid,) = _ids(await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-A"))
        out = {
            "list": await _ctl("sid-B", action="list"),
            "status": await _ctl("sid-B", action="status", id=rid),
            "send": await _ctl("sid-B", action="send", id=rid, message="篡改"),
            "stop": await _ctl("sid-B", action="stop", id=rid),
            "result": await _ctl("sid-B", action="result", id=rid),
            "wait_id": await _ctl("sid-B", action="wait", id=rid, timeout=0.1),
            "wait_all": await _ctl("sid-B", action="wait", timeout=0.1),
            "a_list": await _ctl("sid-A", action="list"),
        }
        rec = reg.get("sid-A", rid)
        assert not rec.stop_requested and not rec.inbox and reg.get("sid-B", rid) is None
        await _ctl("sid-A", action="stop", id=rid)
        await _ctl("sid-A", action="wait", id=rid, timeout=5)
        return rid, out
    rid, out = asyncio.run(go())
    assert out["list"] == "本会话没有子代理记录。"
    for k in ("status", "send", "stop", "result", "wait_id"):
        assert out[k].startswith("ERROR: 找不到子代理") and rid in out[k], (k, out[k])
    assert out["wait_all"] == "本会话没有在运行或待取结果的子代理。"
    assert rid in out["a_list"] and "后台运行中 1/4" in out["a_list"]


# ── 并发上限: 每会话同时运行的后台子代理数可配置, 超过拒绝派发并说明; 别的会话不受影响 ──
def test_background_cap(env, monkeypatch):
    monkeypatch.setitem(CFG, "agent", {**CFG["agent"], "max_background_subagents": 2})
    for k in ("c1", "c2", "c3", "c4", "d1"):
        env.behaviors[k] = _search_forever(k)

    async def go():
        r1 = await _dispatch({"parallel_tasks": [{"task": "c1", "agent_type": "explorer"},
                                                 {"task": "c2", "agent_type": "explorer"}],
                              "background": True}, "sid-C")
        r3 = await _dispatch({"task": "c3", "agent_type": "explorer", "background": True}, "sid-C")
        r4 = await _dispatch({"parallel_tasks": [{"task": "c3", "agent_type": "explorer"},
                                                 {"task": "c4", "agent_type": "explorer"}],
                              "background": True}, "sid-C2")
        r5 = await _dispatch({"dag_tasks": [{"task": "x", "agent_type": "explorer"}], "background": True}, "sid-D")
        for rid in _ids(r1) + _ids(r4):
            sid = "sid-C" if rid in r1 else "sid-C2"
            await _ctl(sid, action="stop", id=rid)
        await _ctl("sid-C", action="wait", timeout=5)
        await _ctl("sid-C2", action="wait", timeout=5)
        return r1, r3, r4, r5
    r1, r3, r4, r5 = asyncio.run(go())
    assert r1.startswith("已在后台派出 2 个子代理") and len(_ids(r1)) == 2
    assert r3.startswith("ERROR: 拒绝派发") and "上限为 2 个" in r3 and "已有 2 个在跑" in r3, r3
    assert r4.startswith("已在后台派出 2 个子代理")                       # 另一个会话单独计数
    assert r5.startswith("ERROR: background=true 只支持单个 task 或 parallel_tasks"), r5


# ── 阻塞模式行为不变 (结果原样), 但也登记到表里便于查看 ──
def test_blocking_unchanged_and_registered(env, monkeypatch):
    from core.tools.handlers.agent import h_spawn_agent
    t = "阻塞任务"
    calls = iter([("tool", "write_file", {"filepath": "g.py", "content": "g = 1\n"}),
                  ("tool", "execute_shell", {"command": "pytest -q"}), ("text", "完成")])

    async def fn(messages, i):
        return next(calls)
    env.behaviors[t] = fn
    out, _ = asyncio.run(h_spawn_agent("sid-K", {"task": t, "agent_type": "coder"}))
    assert out.startswith("[已验证]\n完成\n\n【证据】"), out
    lst = asyncio.run(_ctl("sid-K", action="list"))
    assert "coder「阻塞任务」已完成 阻塞" in lst and "execute_shell(pytest -q)" in lst, lst
    (rid,) = _ids(lst)
    rec = reg.get("sid-K", rid)
    assert rec.fetched and not rec.background and rec.result.startswith("完成")
    assert reg.pending_notice("sid-K") == ""                                 # 阻塞的不算"未取结果"
    # 证据开关关闭时与原行为逐字一致
    monkeypatch.setitem(CFG, "agent", {**CFG["agent"], "subagent_evidence": False})
    sa._SUBAGENT_CACHE.clear()
    t2 = "阻塞任务二"
    env.behaviors[t2] = lambda m, i: _const(("text", "完成"))
    out2, _ = asyncio.run(h_spawn_agent("sid-K", {"task": t2, "agent_type": "coder"}))
    assert out2 == "完成", out2


async def _const(v):
    return v


# ── 回合结束追加备注 (用户可见, 落库); 下一轮注入 [SYSTEM-AGENTS], 不进 web 显示与记忆提取 ──
def test_turn_end_note_and_next_turn_notice(env):
    from lib.agent.turn_phases import _turn_finalize
    from lib.agent.turn_persist import memory_input
    from lib.agent_state import TurnContext, UsageCounters, BatchWriteState
    from lib.guard_stats import GuardStats
    from lib.config import make_tracer
    from lib import turn_context as tc
    import lib.turn_sources  # noqa: F401  注册来源
    t = "长跑任务"

    async def fn(messages, i):
        await env.go.wait()
        return ("tool", "search_code", {"pattern": "z"}) if i == 0 else ("text", "长跑完成")
    env.behaviors[t] = fn
    assert "agents" in tc.sources()

    async def go():
        env.go = asyncio.Event()
        (rid,) = _ids(await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-E"))
        ctx = TurnContext(sid_tag="sid-E", session_id="sid-E", tracer=make_tracer({}),
                          new_msgs=[{"role": "user", "content": "帮我查一下"},
                                    {"role": "assistant", "content": "已派后台子代理去查。"}])
        chunks = [c async for c in _turn_finalize(ctx, UsageCounters(), BatchWriteState(), GuardStats(), "m")]
        # 回合结束时不强杀
        assert reg.get("sid-E", rid).status == "running"
        notice_while_running = reg.pending_notice("sid-E")
        env.go.set()
        await _until(lambda: reg.get("sid-E", rid).status != "running")
        return rid, ctx, chunks, notice_while_running
    rid, ctx, chunks, nwr = asyncio.run(go())
    sse = "".join(chunks)
    assert "还有 1 个后台子代理在运行" in sse and rid in sse and "agent_control" in sse
    assert sse.index("还有 1 个后台子代理") < sse.index("[DONE]") if "[DONE]" in sse else True
    final = ctx.new_msgs[-1]["content"]
    assert final.startswith("已派后台子代理去查。\n\n（系统备注: 还有 1 个后台子代理在运行") and rid in final
    assert nwr == ""                                                          # 还在跑时没有"已结束"提示

    # 下一轮: turn_context 来源给出 [SYSTEM-AGENTS] 提示
    text = reg.turn_source(tc.TurnInput("继续", "sid-E", []))
    assert text.startswith("[SYSTEM-AGENTS] 本会话有 1 个后台子代理已结束, 结果尚未取走") and rid in text, text
    assert reg.turn_source(tc.TurnInput("继续", "sid-OTHER", [])) == ""      # 别的会话看不到
    ctx_text = tc.render([("agents", text)])
    stored = {"role": "user", "content": "继续", tc.KEY: ctx_text}           # 落库形态: content 仍是用户原话
    assert stored["content"] == "继续"                                       # web 显示用 content, 看不到注入
    sent = tc.materialize([stored])[0]
    assert "[SYSTEM-AGENTS]" in sent["content"]                              # 模型看得到
    mem = memory_input([sent], [{"role": "assistant", "content": "好"}])
    assert all("[SYSTEM-AGENTS]" not in str(m.get("content")) for m in mem)   # 记忆提取看不到
    assert reg.turn_source(tc.TurnInput("再继续", "sid-E", [stored])) == ""   # 已给过不重复
    # 取走结果后不再提示
    w = asyncio.run(_ctl("sid-E", action="wait"))
    assert "长跑完成" in w and w.split("\n", 1)[1].startswith("[已验证]")
    assert reg.pending_notice("sid-E") == "" and reg.turn_end_note("sid-E", []) == ""


# ── handler 路径 (execute_tool → h_spawn_agent) 同样支持后台; 过期记录清理 ──
def test_handler_path_and_gc(env, monkeypatch):
    from core.tools.handlers.agent import h_spawn_agent
    t = "handler 后台"
    env.behaviors[t] = _search_forever("H")

    async def go():
        res, _ = await h_spawn_agent("sid-H", {"task": t, "agent_type": "explorer", "background": True})
        (rid,) = _ids(res)
        await _until(lambda: reg.get("sid-H", rid).iteration >= 2)
        await _ctl("sid-H", action="send", id=rid, message="收尾")
        return rid, await _ctl("sid-H", action="wait", id=rid, timeout=5)
    rid, w = asyncio.run(go())
    assert "H: 已按补充指令改用 B 方案" in w and w.split("\n", 1)[1].startswith("[已验证]")
    rec = reg.get("sid-H", rid)
    rec.ended -= reg.RECORD_TTL + 1                    # 已取走 → 过 1 小时清理
    assert reg._gc() == 1 and reg.get("sid-H", rid) is None


def test_user_interrupt_stops_background_agents():
    """用户在界面点停止 (set_interrupt) 时, 该会话运行中的后台子代理一并收到停止请求; 别的会话不受影响。"""
    from lib.agent import subagent_registry as reg
    from lib.session import set_interrupt, clear_interrupt
    a = reg._new_record("sess-a", "coder", "任务A", background=True)
    b = reg._new_record("sess-b", "coder", "任务B", background=True)
    try:
        set_interrupt("sess-a")
        assert a.stop_requested and "停止" in a.stop_reason
        assert not b.stop_requested
        out = None
        token = reg._CURRENT.set(a)
        try:
            out = reg.tick([], 0, 10, "coder")
        finally:
            reg._CURRENT.reset(token)
        assert out and "用户在界面点了停止" in out
    finally:
        clear_interrupt("sess-a")
        with reg._LOCK:
            reg._RECORDS.pop(a.id, None); reg._RECORDS.pop(b.id, None)


# ═══════════ 待跟进修复: 补充指令不被滚动摘要吞掉 / send 告知正在跑的工具 / 后台任务跨回合存活 ═══════════

# ── 端到端: 长补充指令在第 8、16 轮滚动摘要之后仍按原文完整出现在子代理上下文里 (只出现一次) ──
def test_instruction_survives_rolling_summary(env):
    t = "长跑探索"
    long_msg = "改用 B 方案, 注意: " + "边界条件逐个核对; " * 30 + "结尾标记 XYZ-END"

    async def fn(messages, i):
        if i >= 17:
            return ("text", "完成")
        await asyncio.sleep(0.005)
        return ("tool", "search_code", {"pattern": f"P{i}"})
    env.behaviors[t] = fn

    async def go():
        (rid,) = _ids(await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-R"))
        rec = reg.get("sid-R", rid)
        await _until(lambda: rec.iteration >= 2)
        assert (await _ctl("sid-R", action="send", id=rid, message=long_msg)).startswith("已投递")
        return rid, await _ctl("sid-R", action="wait", id=rid, timeout=20)
    rid, w = asyncio.run(go())
    assert "完成" in w, w
    hist = env.seen[t]
    assert len(hist) == 18
    for i in (9, 12, 16, 17):              # 第 8 轮、第 16 轮摘要之后
        blob = "\n".join(str(m.get("content")) for m in hist[i])
        assert blob.count(long_msg) == 1, (i, blob[:600])          # 原文完整且不重复
        assert "以下是前几轮操作的摘要" in blob and "仍然有效的主代理补充指令" in blob
        assert f"[User] {reg.INSTR_PREFIX}" not in blob              # 截断的摘要行已去掉
    assert reg.get("sid-R", rid).instructions == [long_msg]


# ── pin 单元: 去重 / 已在最近消息里不重复 / 总长上限 / 重复压缩不叠加 / 无摘要时插在 system 之后 ──
def test_pin_instructions_unit():
    from lib.agent import subagent_instr as si
    P = reg.INSTR_PREFIX
    summary = {"role": "user", "content": si.SUMMARY_HEAD + "...]\n\n[User] " + P + " 旧指令前八十字\n[Action] x"}
    msgs = [{"role": "system", "content": "sys"}, summary,
            {"role": "assistant", "content": "a"}, {"role": "user", "content": f"{P} 最近的指令 C"}]
    out = si.pin(msgs, ["指令 A", "指令 B", "指令 A", "最近的指令 C"], P)
    s = out[1]["content"]
    assert s.startswith(si.SUMMARY_HEAD) and "[User] " + P not in s
    blk = s.split(si.PIN_HEAD, 1)[1]
    assert blk.count("指令 A") == 1 and "指令 B" in blk and "最近的指令 C" not in blk   # 去重 + 已在最近消息里不附
    assert blk.index("指令 B") < blk.index("指令 A")                                     # 时间顺序 (A 以最近一次为准)
    assert msgs[1] is summary and si.PIN_HEAD not in summary["content"]                  # 不改入参
    again = si.pin(out, ["指令 A", "指令 B", "指令 A", "最近的指令 C"], P)
    assert again[1]["content"].count(si.PIN_HEAD) == 1                                   # 重复压缩不叠加
    # 总长上限: 最新优先, 较早的只记条数
    big = [f"{k}" * 1500 for k in "123"]
    blk2 = si.build_block(big, "", limit=3200)
    assert "3" * 1500 in blk2 and "2" * 1500 in blk2 and "1" * 1500 not in blk2 and "较早的 1 条" in blk2
    # 没有摘要消息 → 作为 user 消息插在 system 之后; 没有指令 → 原样返回
    plain = [{"role": "system", "content": "sys"}, {"role": "assistant", "content": "a"}]
    out3 = si.pin(plain, ["指令 Z"], P)
    assert out3[1]["role"] == "user" and out3[1]["content"].startswith(si.PIN_HEAD) and len(out3) == 3
    assert si.pin(plain, [], P) is plain
    assert reg.pin_instructions(plain) is plain                                          # 非子代理上下文: 不动


# ── send 时子代理正在跑长工具调用: 返回里说明工具名与已运行秒数, 不中断工具, 结束后指令生效 ──
def test_send_reports_running_tool(env, monkeypatch):
    t = "长工具"
    gate = {}
    base_exec = sa.execute_tool

    async def slow_exec(name, args, sid):
        if args.get("pattern") == "SLOW":
            gate["in"].set()
            await gate["go"].wait()
        return await base_exec(name, args, sid)
    monkeypatch.setattr(sa, "execute_tool", slow_exec)

    async def fn(messages, i):
        if any(reg.INSTR_PREFIX in str(m.get("content")) for m in messages):
            return ("text", "按指令收尾")
        return ("tool", "search_code", {"pattern": "SLOW"})
    env.behaviors[t] = fn

    async def go():
        gate["in"], gate["go"] = asyncio.Event(), asyncio.Event()
        (rid,) = _ids(await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-L"))
        await gate["in"].wait()
        reg.get("sid-L", rid).tool_started -= 7              # 假装已跑 7 秒
        st = await _ctl("sid-L", action="status", id=rid)
        s = await _ctl("sid-L", action="send", id=rid, message="跑完这个就收尾")
        assert reg.get("sid-L", rid).status == "running"     # 工具没被打断
        gate["go"].set()
        w = await _ctl("sid-L", action="wait", id=rid, timeout=5)
        return rid, st, s, w, await _ctl("sid-L", action="status", id=rid)
    rid, st, s, w, st2 = asyncio.run(go())
    assert "正在执行 search_code(SLOW) 已运行 7 秒" in st, st
    assert s.startswith(f"已投递给 {rid}") and "当前工具调用 search_code(SLOW) 已运行 7 秒" in s, s
    assert "指令将在本轮工具调用结束后" in s and "不会强行中断工具" in s, s
    assert "按指令收尾" in w and "正在执行" not in st2


# ── 后台任务跨回合存活: 服务端 (uvicorn) 是常驻事件循环, 每轮请求是独立 task;
#    派发那一轮的请求 task 结束甚至被取消 (客户端断开/CLI Ctrl-C) 都不影响后台子代理, 下一轮能 wait 到结果 ──
def test_background_survives_request_task_on_persistent_loop(env):
    t = "跨回合"

    async def fn(messages, i):
        await env.go.wait()
        return ("text", "跨回合完成")
    env.behaviors[t] = fn

    async def go():
        env.go = asyncio.Event()
        box = {}

        async def turn1():                     # 第一轮请求: 派发后客户端断开
            box["res"] = await _dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-X")
            await asyncio.sleep(10)
        req = asyncio.ensure_future(turn1())
        await _until(lambda: "res" in box)
        req.cancel()
        with pytest.raises(asyncio.CancelledError):
            await req
        (rid,) = _ids(box["res"])
        assert reg.get("sid-X", rid).status == "running"
        env.go.set()
        turn2 = asyncio.ensure_future(_ctl("sid-X", action="wait", id=rid, timeout=5))   # 第二轮请求
        return rid, await turn2
    rid, w = asyncio.run(go())
    assert "跨回合完成" in w and reg.get("sid-X", rid).status == "done"


# ── 每轮各自 asyncio.run (事件循环随轮次关闭) 的情形: 后台子代理会被取消, 但必须明确记成 stopped 并给出原因,
#    不能永远显示"运行中"; 且 CLI 走 HTTP 调常驻服务, 自身不在进程内跑 agent 循环 (守住这个前提) ──
def test_loop_close_marks_stopped_and_cli_is_http_client(env):
    t = "随循环关闭"

    async def fn(messages, i):
        await asyncio.sleep(30)
        return ("text", "不会到这")
    env.behaviors[t] = fn
    res = asyncio.run(_dispatch({"task": t, "agent_type": "explorer", "background": True}, "sid-Y"))
    (rid,) = _ids(res)
    rec = reg.get("sid-Y", rid)
    assert rec.status == "stopped" and "后台任务被取消" in rec.result, (rec.status, rec.result)
    for f in ("cli.py", "litecli.py", "litecode.py"):
        src = (ROOT / f).read_text("utf-8")
        assert "asyncio.run(" not in src and "agent_stream" not in src and "import litecode_server" not in src, f
