"""2026-10-21 运行中插话: 任务跑着也能发消息, 不用先停止。
普通插话在两步之间接进上下文; 立即插话放弃正在执行的工具、跳过本批剩余工具, 但不结束回合。"""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(ROOT / "lib")]

from lib import session as S  # noqa: E402
from lib.agent import steer as ST  # noqa: E402


def _ctx(sid):
    return SimpleNamespace(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "部署 27B"}],
                           base_history=[], new_msgs=[], session_id=sid, sid_tag="st", disabled_tools=set(),
                           turn_context="",
                           tracer=SimpleNamespace(tool_call=lambda **kw: None, get_loop_hint=lambda: "",
                                                  should_force_break=lambda: False, reset_loop=lambda: None))


def test_queue_and_apply_as_user_message():
    sid = "steer-q"
    S.take_steers(sid)
    S.push_steer(sid, "pip 用清华源")
    S.push_steer(sid, "测完别删容器")
    assert S.has_steers(sid) and not S.steer_urgent(sid)
    ctx = _ctx(sid)
    texts = ST.apply_steers(ctx)
    assert texts == ["pip 用清华源", "测完别删容器"]
    msg = ctx.messages[-1]
    assert msg["role"] == "user" and msg["content"].startswith(ST.STEER_HEAD)
    assert "- pip 用清华源" in msg["content"] and "不要从头重来" in msg["content"]
    assert ctx.new_msgs[-1] is msg                      # 入库, 刷新后还在
    assert not S.has_steers(sid) and ST.apply_steers(ctx) == []


def _run_tools(monkeypatch, sid, calls, slow_s, urgent_after):
    from lib.agent import tool_exec
    ctx = _ctx(sid)
    monkeypatch.setattr(tool_exec, "_guard_tool_call", lambda *a: None)
    executed = []

    async def execute(name, args, sid=None):
        executed.append(name)
        await asyncio.sleep(slow_s)
        return "done", None
    monkeypatch.setattr(tool_exec, "execute_tool", execute)
    state = tool_exec.ToolRunState(effective_timeout=60, error_streak=0, iter_write_success=False)
    tcs = [{"id": f"c{i}", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    results = []

    async def run():
        async def steerer():
            await asyncio.sleep(urgent_after)
            S.push_steer(sid, "别从国外源装, 用清华源", urgent=True)
        t = asyncio.ensure_future(steerer())
        async for _ in tool_exec._run_tool_calls(
                ctx, state, tcs, results, SimpleNamespace(action_tool_calls=0),
                None, None, None, SimpleNamespace(take=lambda *a: None),
                None, None, [], SimpleNamespace(check=lambda *a: (False, None, "sig")),
                SimpleNamespace(t0=time.time()), False, "test-model"):
            pass
        await t
    t0 = time.time()
    asyncio.run(run())
    return ctx, results, executed, time.time() - t0


def test_urgent_steer_abandons_tool_but_keeps_turn(monkeypatch):
    sid = "steer-u"
    S.take_steers(sid)
    try:
        ctx, results, executed, took = _run_tools(
            monkeypatch, sid, [("ssh_exec", {"host": "gpu1", "command": "pip3 install modelscope"}),
                               ("ssh_exec", {"host": "gpu1", "command": "ls"})], slow_s=30, urgent_after=0.3)
        assert took < 5 and executed == ["ssh_exec"]
        assert "立即插话" in results[0]["content"] and "未执行" in results[1]["content"]
        assert not S.check_interrupt(sid)               # 不是停止: 回合不结束
        assert ST.apply_steers(ctx) == ["别从国外源装, 用清华源"]
        assert not S.steer_urgent(sid)                  # 取走插话时 urgent 一并清掉
    finally:
        S.take_steers(sid)


def test_server_injects_after_tools_and_before_ending():
    src = (ROOT / "litecode_server.py").read_text(encoding="utf-8")
    after_tools = src.index("_after_tool_batch(_ctx, _guard_stats")
    inject = src.index("_steered = _apply_steers(_ctx)", after_tools)
    assert inject < src.index('_ctx.tracer.iteration_end("ITER_TOOL_CALL"', after_tools)
    tail = src.index('if _tr.action == "break":')
    assert src.index("_apply_steers(_ctx)", tail) < src.index("else:   # for 正常耗尽")
    assert "if _iteration == 0:\n                    _apply_steers(_ctx)" in src


def test_gateway_only_queues_when_turn_active():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import gateway.sessions as GS
    monkey_auth = GS.require_auth
    GS.require_auth = lambda r: None
    try:
        app = FastAPI()
        app.include_router(GS.router)
        c = TestClient(app)
        sid = "steer-g"
        S.take_steers(sid)
        r = c.post(f"/v1/sessions/{sid}/steer", json={"text": "补一句"}).json()
        assert r["queued"] is False and not S.has_steers(sid)       # 没有进行中的回合: 不排队
        lock = S.get_session_lock(sid)

        async def hold():
            await lock.acquire()
        asyncio.run(hold())
        try:
            r = c.post(f"/v1/sessions/{sid}/steer", json={"text": "补一句", "urgent": True}).json()
            assert r["queued"] is True and r["urgent"] is True
            assert S.steer_urgent(sid) and S.take_steers(sid) == ["补一句"]
        finally:
            lock.release()
    finally:
        GS.require_auth = monkey_auth


def test_frontend_send_during_run_goes_to_steer():
    js = (ROOT / "web_assets" / "chat_stream.js").read_text(encoding="utf-8")
    assert "if(streaming){return steerSend(false);}" in js and "_lcSteerInline(msg,urgent)" in js
    assert "document.getElementById('sdb').disabled=true;document.getElementById('ib')" not in js
    html = (ROOT / "web_ui.html").read_text(encoding="utf-8")
    assert 'id="stb" onclick="steerSend(true)"' in html


def test_normal_steer_lets_current_tool_finish_but_holds_rest_of_batch(monkeypatch):
    """模型常把几条命令放一批; 普通插话不打断正在跑的那条, 但剩下的不再等 (实测原来要等 55s)。"""
    sid = "steer-n"
    S.take_steers(sid)
    from lib.agent import tool_exec
    ctx = _ctx(sid)
    monkeypatch.setattr(tool_exec, "_guard_tool_call", lambda *a: None)
    executed = []

    async def execute(name, args, sid=None):
        executed.append(args["command"])
        await asyncio.sleep(0.6)
        return "out-" + args["command"], None
    monkeypatch.setattr(tool_exec, "execute_tool", execute)
    state = tool_exec.ToolRunState(effective_timeout=60, error_streak=0, iter_write_success=False)
    tcs = [{"id": f"c{i}", "function": {"name": "execute_shell", "arguments": json.dumps({"command": c})}}
           for i, c in enumerate(["A", "B", "C"])]
    results = []

    async def run():
        async def steerer():
            await asyncio.sleep(0.2)
            S.push_steer(sid, "最后一条改成 C-CHANGED")
        t = asyncio.ensure_future(steerer())
        async for _ in tool_exec._run_tool_calls(
                ctx, state, tcs, results, SimpleNamespace(action_tool_calls=0),
                None, None, None, SimpleNamespace(take=lambda *a: None),
                None, None, [], SimpleNamespace(check=lambda *a: (False, None, "sig")),
                SimpleNamespace(t0=time.time()), False, "test-model"):
            pass
        await t
    try:
        asyncio.run(run())
        assert executed == ["A"]                                   # 正在跑的 A 跑完, B/C 暂缓
        assert "out-A" in results[0]["content"]
        assert all("暂缓" in r["content"] for r in results[1:]) and len(results) == 3
        assert ST.apply_steers(ctx) == ["最后一条改成 C-CHANGED"]
    finally:
        S.take_steers(sid)
