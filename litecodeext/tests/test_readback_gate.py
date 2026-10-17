"""收尾前回读闸 (lib/agent/readback_gate.py) 的离线回归。

驱动真实 agent_stream(): 在 lib.agent.llm_call._vllm_stream / lib.agent.tool_exec.execute_tool 处打桩
(拆分后真正查名字的模块), 不连任何服务、不碰真实节点。
真机案例: WPS 表格 D5=SUM(D2:D4) 实际 1964, 模型按截图汇报 "合计 4163 ✓ 已验证"。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import litecode_server as srv  # noqa: E402
import lib.agent.llm_call as _llm_call  # noqa: E402
import lib.agent.tool_exec as _tool_exec  # noqa: E402
from lib.agent import readback_gate as rb  # noqa: E402
from lib.agent.turn_persist import memory_input  # noqa: E402

USER = "在 gpu-vm 上用 WPS 表格做一张销售表, 算出合计"
WRONG = "表格已完成, 合计 4163 ✓ 已验证。"
RIGHT = "表格已完成, 文件回读核对: D5=SUM(D2:D4)=1964。"

DESKTOP_EDIT = ("windows_desktop", {"alias": "gpu-vm", "action": "hotkey", "keys": "ctrl+v"})   # 粘贴进单元格
DESKTOP_SAVE = ("windows_desktop", {"alias": "gpu-vm", "action": "hotkey", "keys": "ctrl+s"})
DESKTOP_SHOT = ("windows_desktop", {"alias": "gpu-vm", "action": "screenshot"})
INSPECT = ("windows_system", {"alias": "gpu-vm", "action": "inspect_file", "path": r"%USERPROFILE%\Desktop\销售表.xlsx"})


class _FakeLLM:
    """按脚本回放; 脚本走完重复最后一项。记录每次收到的 messages 副本。"""
    def __init__(self, turns):
        self.turns, self.calls, self.seen = turns, 0, []

    async def __call__(self, messages, tools, max_tokens_override=None):
        self.seen.append(json.loads(json.dumps(messages, ensure_ascii=False, default=str)))
        kind, payload = self.turns[min(self.calls, len(self.turns) - 1)]
        idx = self.calls
        self.calls += 1
        if kind == "text":
            yield {"choices": [{"delta": {"content": payload}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        else:
            name, args = payload
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"c{idx}",
                   "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]},
                   "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}


@pytest.fixture
def env(monkeypatch):
    calls = []

    async def _fake_tool(name, args, sid=None):
        calls.append((name, args.get("action")))
        if name == "windows_system" and args.get("action") == "inspect_file":
            return json.dumps({"ok": True, "type": "xlsx", "formulas": [
                {"sheet": "销售", "addr": "D5", "formula": "=SUM(D2:D4)", "value": 1964}]}, ensure_ascii=False), None
        if name == "windows_desktop":
            return json.dumps({"ok": True, "action": args.get("action")}), None
        return "ok", None
    monkeypatch.setattr(srv, "execute_tool", _fake_tool)
    monkeypatch.setattr(_tool_exec, "execute_tool", _fake_tool)
    captured = {}

    def _fake_persist(ctx, *_a):
        captured["new_msgs"] = list(ctx.new_msgs)
        captured["base_history"] = list(ctx.base_history)
    monkeypatch.setattr(srv, "_turn_persist", _fake_persist)
    _set_gate(monkeypatch, True)
    return calls, captured


def _set_gate(monkeypatch, on):
    agent = dict(rb.CFG.get("agent") or {})
    agent["readback_gate"] = on
    monkeypatch.setitem(rb.CFG, "agent", agent)


def _drive(monkeypatch, turns, user_message=USER):
    fake = _FakeLLM(turns)
    monkeypatch.setattr(srv, "_vllm_stream", fake)
    monkeypatch.setattr(_llm_call, "_vllm_stream", fake)

    async def _run():
        return "".join([c async for c in srv.agent_stream(user_message, None)])
    return asyncio.run(_run()), fake


def _injections(fake):
    """模型各轮实际收到的 user 消息里 [SYSTEM-READBACK] 出现的次数 (按最后一次调用的完整上下文算)。"""
    return [m for m in fake.seen[-1] if m.get("role") == "user" and rb.PREFIX in str(m.get("content"))]


def test_a_edit_then_summary_is_gated_once_then_readback_ends(env, monkeypatch):
    calls, captured = env
    turns = [("tool", DESKTOP_EDIT), ("tool", DESKTOP_SAVE), ("text", WRONG), ("tool", INSPECT), ("text", RIGHT)]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == 5 and "[DONE]" in full
    inj = _injections(fake)
    assert len(inj) == 1 and inj[0]["content"] == rb.INJECT_TEXT
    assert "inspect_file" in inj[0]["content"] and "数字" in inj[0]["content"]
    assert ("windows_system", "inspect_file") in calls
    assert captured["new_msgs"][-1] == {"role": "assistant", "content": RIGHT}


def test_b_already_read_back_no_injection(env, monkeypatch):
    turns = [("tool", DESKTOP_EDIT), ("tool", DESKTOP_SAVE), ("tool", INSPECT), ("text", RIGHT)]
    _, fake = _drive(monkeypatch, turns)
    assert fake.calls == 4 and not _injections(fake)


def test_b2_observe_only_desktop_no_injection(env, monkeypatch):
    """只截图/OCR 不改东西 → 不拦。"""
    _, fake = _drive(monkeypatch, [("tool", DESKTOP_SHOT), ("text", "屏幕上是桌面。")])
    assert fake.calls == 2 and not _injections(fake)


def test_b3_edit_after_readback_gates_again(env, monkeypatch):
    """回读之后又改了 → 最后一次改动之后没有回读, 仍要拦。"""
    turns = [("tool", DESKTOP_EDIT), ("tool", INSPECT), ("tool", DESKTOP_SAVE), ("text", WRONG),
             ("tool", INSPECT), ("text", RIGHT)]
    _, fake = _drive(monkeypatch, turns)
    assert fake.calls == 6 and len(_injections(fake)) == 1


def test_c_no_windows_desktop_no_injection(env, monkeypatch):
    _, fake = _drive(monkeypatch, [("tool", ("windows_system", {"alias": "gpu-vm", "action": "system_info"})),
                                   ("text", "系统信息如上。")])
    assert fake.calls == 2 and not _injections(fake)


def test_d_switch_off_no_injection(env, monkeypatch):
    _set_gate(monkeypatch, False)
    turns = [("tool", DESKTOP_EDIT), ("tool", DESKTOP_SAVE), ("text", WRONG), ("text", "不该被调用")]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == 3 and not _injections(fake)
    assert "不该被调用" not in full


def test_e_model_refuses_second_summary_passes(env, monkeypatch):
    calls, captured = env
    turns = [("tool", DESKTOP_EDIT), ("text", WRONG), ("text", "确实没有产出文件, 只是在聊天窗口输入了文字。"),
             ("text", "不该被调用")]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == 3, "第二次总结必须放行, 不能死循环"
    assert len(_injections(fake)) == 1
    assert captured["new_msgs"][-1]["content"].startswith("确实没有产出文件")


def test_failed_readback_does_not_count(env, monkeypatch):
    """回读调用本身报错 (ERROR) 不算回读, 收尾时仍会提醒一次。"""
    async def _tool(name, args, sid=None):
        if name == "windows_system":
            return "ERROR: windows_system RuntimeError: 读取远端文件失败", None
        return json.dumps({"ok": True}), None
    monkeypatch.setattr(srv, "execute_tool", _tool)
    monkeypatch.setattr(_tool_exec, "execute_tool", _tool)
    turns = [("tool", DESKTOP_EDIT), ("tool", INSPECT), ("text", WRONG), ("text", "文件取不回来, 只能按截图汇报。")]
    _, fake = _drive(monkeypatch, turns)
    assert fake.calls == 4 and len(_injections(fake)) == 1


def test_f_injection_hidden_from_web_and_memory(env, monkeypatch):
    calls, captured = env
    turns = [("tool", DESKTOP_EDIT), ("text", WRONG), ("tool", INSPECT), ("text", RIGHT)]
    _drive(monkeypatch, turns)
    new_msgs = captured["new_msgs"]
    assert any(m.get("role") == "user" and m.get("content") == rb.INJECT_TEXT for m in new_msgs)
    # Web: [SYSTEM-...] 伪 user 消息不进界面
    import web_ui
    assert web_ui._is_sys_inject_text(rb.INJECT_TEXT)
    web = web_ui._convert_server_session("t-readback", {"msgs": new_msgs, "ts": 0})
    blob = json.dumps(web, ensure_ascii=False)
    assert rb.PREFIX not in blob and USER in blob and RIGHT in blob
    # 记忆提取: 输入里剥掉注入, 用户原话保留
    mem = memory_input(captured["base_history"], new_msgs)
    assert not any(rb.PREFIX in str(m.get("content")) for m in mem)
    assert mem[0]["role"] == "user" and mem[0]["content"] == USER


def test_persist_feeds_memory_without_injection(monkeypatch):
    """_turn_persist 真实路径: 交给记忆线程的消息里不含 [SYSTEM-READBACK]。"""
    from types import SimpleNamespace
    import lib.agent.turn_persist as tp
    got = []
    monkeypatch.setattr(tp, "_save_history", lambda *a, **k: None)
    monkeypatch.setattr(tp, "HAS_MEMORY", True)
    monkeypatch.setattr(tp, "threading", SimpleNamespace(
        Thread=lambda target, args, daemon: SimpleNamespace(start=lambda: got.append(args[0]))))
    ctx = SimpleNamespace(session_id="s-rb", sid_tag="s-rb", base_history=[],
                          new_msgs=[{"role": "user", "content": USER},
                                    {"role": "assistant", "content": WRONG},
                                    {"role": "user", "content": rb.INJECT_TEXT},
                                    {"role": "assistant", "content": RIGHT}])
    tp._turn_persist(ctx, SimpleNamespace(), SimpleNamespace(project_root=None), SimpleNamespace())
    assert len(got) == 3
    for msgs in got:
        assert [m["content"] for m in msgs] == [USER, WRONG, RIGHT]


def test_observe_same_batch_order():
    """同一批里先改后读算已回读; 先读后改不算。"""
    from types import SimpleNamespace

    def tc(i, name, args):
        return {"id": f"x{i}", "function": {"name": name, "arguments": json.dumps(args)}}
    ok = [{"tool_call_id": "x0", "content": "{}"}, {"tool_call_id": "x1", "content": "{}"}]
    ctx = SimpleNamespace()
    rb.observe(ctx, [tc(0, *DESKTOP_EDIT), tc(1, *INSPECT)], ok)
    assert rb.check(ctx) is None
    ctx = SimpleNamespace()
    rb.observe(ctx, [tc(0, *INSPECT), tc(1, *DESKTOP_EDIT)], ok)
    assert rb.check(ctx) == rb.INJECT_TEXT and rb.check(ctx) is None


def test_chat_typing_does_not_trigger(env, monkeypatch):
    """在聊天窗口打字 + 回车 + 点击 不产出文件: 不拦, 一次就能收尾 (2026-10-17 收窄触发条件)。"""
    calls, captured = env
    chat = [("tool", ("windows_desktop", {"alias": "gpu-vm", "action": "click", "x": 10, "y": 10})),
            ("tool", ("windows_desktop", {"alias": "gpu-vm", "action": "type_text", "text": "晚上好"})),
            ("tool", ("windows_desktop", {"alias": "gpu-vm", "action": "hotkey", "keys": "enter"})),
            ("text", "消息已发送。")]
    _, fake = _drive(monkeypatch, chat)
    assert len(_injections(fake)) == 0 and fake.calls == 4


def test_file_change_classification():
    assert rb.is_file_change("hotkey", {"keys": "ctrl+s"}) and rb.is_file_change("hotkey", {"keys": "F2"})
    assert rb.is_file_change("drag", {}) and rb.is_file_change("click_text", {"text": "另存为"})
    assert not rb.is_file_change("hotkey", {"keys": "enter"}) and not rb.is_file_change("type_text", {"text": "x"})
    assert not rb.is_file_change("click_text", {"text": "发送"}) and not rb.is_file_change("click", {})
