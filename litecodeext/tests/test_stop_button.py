"""2026-10-21: 点停止后 LiteCode 还在等 ssh_exec 跑完远程 pip install (几分钟), 停止像没生效。
工具执行中每秒查停止标记, 命中即放弃当前工具、跳过本批剩余工具; 停止接口同时停掉后台进程。"""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(ROOT / "lib")]

from lib import session as S  # noqa: E402


def _run(monkeypatch, sid, calls, slow_s, stop_after):
    from lib.agent import tool_exec
    ctx = SimpleNamespace(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "跑一下"}],
                          base_history=[], new_msgs=[], session_id=sid, sid_tag="st", disabled_tools=set(),
                          turn_context="",
                          tracer=SimpleNamespace(tool_call=lambda **kw: None, get_loop_hint=lambda: "",
                                                 should_force_break=lambda: False, reset_loop=lambda: None))
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
        async def stopper():
            await asyncio.sleep(stop_after)
            with S._interrupt_lock:
                S._interrupt_flags.add(sid)
        t = asyncio.ensure_future(stopper())
        async for _ in tool_exec._run_tool_calls(
                ctx, state, tcs, results, SimpleNamespace(action_tool_calls=0),
                None, None, None, SimpleNamespace(take=lambda *a: None),
                None, None, [], SimpleNamespace(check=lambda *a: (False, None, "sig")),
                SimpleNamespace(t0=time.time()), False, "test-model"):
            pass
        await t
    t0 = time.time()
    asyncio.run(run())
    return results, executed, time.time() - t0


def test_stop_abandons_running_tool_and_skips_rest(monkeypatch):
    sid = "stop-1"
    try:
        results, executed, took = _run(monkeypatch, sid,
                                       [("ssh_exec", {"host": "gpu1", "command": "pip install x"}),
                                        ("execute_shell", {"command": "ls"})], slow_s=30, stop_after=0.3)
        assert took < 5                                          # 原来要等工具跑满 30s
        assert executed == ["ssh_exec"]                          # 第二个工具没执行
        assert len(results) == 2                                 # tool_call 与结果仍成对
        assert "用户已点停止" in results[0]["content"] and "目标机上继续运行" in results[0]["content"]
        assert "未执行" in results[1]["content"]
        assert S.check_interrupt(sid)                            # 标记留给外层统一收尾
    finally:
        S.clear_interrupt(sid)


def test_no_stop_runs_normally(monkeypatch):
    results, executed, _ = _run(monkeypatch, "stop-2", [("execute_shell", {"command": "ls"})],
                                slow_s=0.1, stop_after=5)
    assert executed == ["execute_shell"] and "用户已点停止" not in results[0]["content"]


def test_interrupt_api_stops_bg_procs(monkeypatch):
    import tools.bg as BG
    stopped = []

    async def fake_cleanup(sid, force=False):
        stopped.append((sid, force))
        return 0, []
    monkeypatch.setattr(BG, "cleanup_session_bg", fake_cleanup)

    async def run():
        S.set_interrupt("stop-3")
        await asyncio.sleep(0.05)
    try:
        asyncio.run(run())
    finally:
        S.clear_interrupt("stop-3")
    assert stopped == [("stop-3", False)]                         # keep_alive 的进程不强杀
    src = (ROOT / "gateway" / "sessions.py").read_text(encoding="utf-8")
    assert "set_interrupt(sid)" in src                            # 停止接口走统一入口
