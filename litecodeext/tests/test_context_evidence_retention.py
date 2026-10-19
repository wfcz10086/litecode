"""模型上下文裁剪保留原始会话证据；覆盖共享消息对象和真实 WAL/收尾落盘。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib import session
from lib.agent import loop_budget, tool_exec


def _shared_context(messages):
    # 与主循环相同：模型列表、旧历史和本轮待落盘列表共享消息 dict。
    return SimpleNamespace(messages=list(messages), base_history=messages[:6],
                           new_msgs=messages[6:])


def _assert_original_persisted(ctx, original, tmp_path, monkeypatch):
    assert ctx.base_history + ctx.new_msgs == original
    monkeypatch.setattr(session, "SESSIONS_DISK", tmp_path)
    monkeypatch.setattr(session, "WORKSPACE", tmp_path)
    monkeypatch.setattr(session, "_sessions", {})
    sid = "evidence-retention"
    # 裁剪后才 flush 的待提交对象也必须原样写进 WAL。
    session.wal_append(sid, ctx.new_msgs)
    assert session.wal_load(sid) == original[6:]
    session.save_history(sid, ctx.base_history, ctx.new_msgs)
    assert session.disk_load(sid)["msgs"] == original
    assert not session._swal(sid).exists()


def _messages(count=30):
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "task"}]
    for i in range(count):
        role = ("assistant", "tool", "user")[i % 3]
        content = ("[SYSTEM] " if role == "user" else f"evidence {i}\n") + "x" * 1500
        messages.append({"role": role, "content": content})
    return messages


def test_sliding_window_keeps_original_results_and_assistant_text(tmp_path, monkeypatch):
    messages = _messages()
    original = deepcopy(messages)
    ctx = _shared_context(messages)
    assert ctx.messages[3] is ctx.base_history[3]
    assert ctx.messages[6] is ctx.new_msgs[0]
    monkeypatch.setattr(loop_budget, "_AGT", {"sliding_window_every": 1})
    monkeypatch.setattr(loop_budget._cfg, "CONTEXT_WINDOW", 10**7)
    monkeypatch.setattr(loop_budget._cp, "under_pressure", lambda *a: True)
    monkeypatch.setattr(loop_budget._oc, "adjust", lambda *a: 0)
    usage = SimpleNamespace(usage_reports=0)

    loop_budget._loop_context_window(ctx, usage, iteration=1, sys_prompt="system")

    assert ctx.messages[2]["content"] == original[2]["content"][:150] + "..."
    assert ctx.messages[3]["content"] == "evidence 1 [trimmed]"
    assert ctx.messages[-14:] == original[-14:]
    _assert_original_persisted(ctx, original, tmp_path, monkeypatch)


def test_context_budget_only_trims_model_copy(tmp_path, monkeypatch):
    messages = _messages()
    original = deepcopy(messages)
    ctx = _shared_context(messages)
    monkeypatch.setattr(loop_budget._cfg, "CONTEXT_WINDOW", 8000)

    loop_budget._loop_context_budget(ctx, fixed_overhead=0)

    assert ctx.messages[2]["content"] == original[2]["content"][:120] + "..."
    assert ctx.messages[3]["content"] == "evidence 1 [budget-trimmed]"
    assert ctx.messages[4]["content"] == original[4]["content"][:80] + " [trimmed]"
    assert ctx.messages[-8:] == original[-8:]
    _assert_original_persisted(ctx, original, tmp_path, monkeypatch)


def test_hard_budget_sanitization_does_not_rewrite_archived_tool_calls(tmp_path, monkeypatch):
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "task"}]
    messages += [{"role": "assistant", "content": "old evidence " + "x" * 1500}
                 for _ in range(25)]
    tail = {"role": "assistant", "content": "pending call", "tool_calls": [
        {"id": "unanswered", "function": {"name": "read_file", "arguments": {"path": "app.py"}}}
    ]}
    messages += [tail] + [{"role": "user", "content": "recent"} for _ in range(9)]
    original = deepcopy(messages)
    ctx = _shared_context(messages)
    monkeypatch.setattr(loop_budget, "_AGT", {"token_budget_pct": 0.6})
    monkeypatch.setattr(loop_budget._cfg, "CONTEXT_WINDOW", 8000)
    monkeypatch.setattr(loop_budget._oc, "adjust", lambda *a: 0)

    loop_budget._loop_context_window(ctx, SimpleNamespace(usage_reports=0),
                                     iteration=1, sys_prompt="system")

    assert len(ctx.messages) < len(original)
    model_tail = next(m for m in ctx.messages if m.get("content") == "pending call")
    assert "tool_calls" not in model_tail
    assert tail["tool_calls"][0]["function"]["arguments"] == {"path": "app.py"}
    _assert_original_persisted(ctx, original, tmp_path, monkeypatch)


def test_loop_clear_keeps_pending_raw_tool_results(tmp_path, monkeypatch):
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "task"}]
    messages += [{"role": "tool", "tool_call_id": f"old-{i}",
                  "content": f"original result {i}: " + "x" * 300}
                 for i in range(6)]
    original = deepcopy(messages)
    ctx = _shared_context(messages)
    ctx.session_id = "evidence-retention"
    ctx.sid_tag = "evidence"
    ctx.disabled_tools = set()
    resets = []
    ctx.tracer = SimpleNamespace(tool_call=lambda **kw: None, get_loop_hint=lambda: "",
                                should_force_break=lambda: True,
                                reset_loop=lambda: resets.append(True))
    monkeypatch.setattr(tool_exec, "_guard_tool_call", lambda *a: None)
    monkeypatch.setattr(tool_exec, "_is_action_tool", lambda name: False)

    async def execute(*args, **kwargs):
        return "ok", None

    monkeypatch.setattr(tool_exec, "execute_tool", execute)
    state = tool_exec.ToolRunState(effective_timeout=60, error_streak=0, iter_write_success=False)
    tc = {"id": "new-call", "function": {"name": "test_probe", "arguments": json.dumps({})}}
    results = []

    async def run():
        async for _ in tool_exec._run_tool_calls(
                ctx, state, [tc], results, SimpleNamespace(action_tool_calls=0),
                None, None, None, SimpleNamespace(take=lambda *a: None),
                None, None, [], SimpleNamespace(check=lambda *a: (False, None, "sig")),
                SimpleNamespace(t0=time.time()), False, "test-model"):
            pass

    asyncio.run(run())

    assert resets == [True]
    assert ctx.messages[2:5] == original[2:5]
    assert all(m["content"].startswith("[CLEARED: loop detected,") for m in ctx.messages[5:])
    assert "[SYSTEM OVERRIDE]" in results[0]["content"]          # 2026-10-19: 先保留本次真实结果, 再附强制换策略提示
    assert results[0]["content"].startswith("ok")
    _assert_original_persisted(ctx, original, tmp_path, monkeypatch)


def test_per_iteration_sanitize_keeps_raw_tool_call_evidence(tmp_path, monkeypatch):
    # [2026-10-19] 主循环每轮 _sanitize_history(_ctx.messages) 原地改写与 new_msgs 共享的 dict:
    # content None→"", dict arguments 被转成字符串、坏 JSON 变 "{}", 未答复的 tool_calls 被删, 落盘证据失真。
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "task"}]
    messages += [{"role": "user", "content": f"old {i}"} for i in range(4)]
    messages += [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "answered", "function": {"name": "read_file", "arguments": {"path": "app.py"}}},
            {"id": "unanswered", "function": {"name": "ssh_exec", "arguments": "{\"cmd\": \"ls\""}}]},
        {"role": "tool", "tool_call_id": "answered", "content": "file body"},
        {"role": "tool", "tool_call_id": "orphan", "content": "orphan result"},
    ]
    original = deepcopy(messages)
    ctx = _shared_context(messages)

    ctx.messages = loop_budget.sanitize_model_copy(ctx.messages)

    model_asst = next(m for m in ctx.messages if m.get("role") == "assistant")
    assert model_asst["content"] == ""
    assert [tc["id"] for tc in model_asst["tool_calls"]] == ["answered"]
    assert model_asst["tool_calls"][0]["function"]["arguments"] == '{"path": "app.py"}'
    assert not any(m.get("tool_call_id") == "orphan" for m in ctx.messages)
    _assert_original_persisted(ctx, original, tmp_path, monkeypatch)


def test_main_loop_no_longer_sanitizes_shared_messages_in_place():
    src = (ROOT / "litecode_server.py").read_text(encoding="utf-8")
    assert "\n                _sanitize_history(_ctx.messages)\n" not in src
    assert "_ctx.messages = _sanitize_model_copy(_ctx.messages)" in src
