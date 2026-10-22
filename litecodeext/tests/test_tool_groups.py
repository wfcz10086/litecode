"""低频工具组按需暴露 (lib/tool_groups.py): 核心常驻 / 触发后能拿到 / 同一会话列表稳定且只增不减。"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import pytest  # noqa: E402

import lib.config as cfg  # noqa: E402
from lib import tool_groups as tg  # noqa: E402
from lib.tools.defs import TOOL_DEFS  # noqa: E402

ALL = [t["function"]["name"] for t in TOOL_DEFS]
CORE_MUST = {"execute_shell", "read_file", "write_file", "patch_file", "web_search", "web_fetch", "browser_read",
             "vision_ocr", "spawn_agent", "agent_control", "load_skill", "save_memory", "update_profile",
             "task_create", "task_update", "task_done", "list_bg", "kill_bg", "find_files", "search_code",
             "enable_tools"}


def _names(defs):
    return [t["function"]["name"] for t in defs]


@pytest.fixture
def sess(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "SESSIONS_DISK", tmp_path)
    monkeypatch.setattr(tg, "_aliases", lambda: {})
    sid = "tg-test-session"
    (tmp_path / sid).mkdir()
    tg.forget(sid)
    yield sid
    tg.forget(sid)


def _ctx(sid, msgs):
    return SimpleNamespace(session_id=sid, messages=msgs)


def test_groups_cover_real_tools_and_core_is_rest():
    grouped = set(tg.TOOL_TO_GROUP)
    assert grouped <= set(ALL), f"组里写了不存在的工具: {grouped - set(ALL)}"
    core = _names(tg.select(TOOL_DEFS, ()))
    assert CORE_MUST <= set(core)
    assert not (set(core) & grouped)
    assert set(core) | grouped == set(ALL)          # 不丢任何工具
    assert _names(tg.select(TOOL_DEFS, tg.GROUPS)) and set(_names(tg.select(TOOL_DEFS, tg.GROUPS))) == set(ALL)


@pytest.mark.parametrize("msg,want", [
    ("在 Windows 电脑 win-hugebq 上打开记事本输入一行字", {"windows"}),
    ("帮我建一个定时任务：每天早上 9:00 提醒我喝水", {"workflow"}),
    ("ssh 到 gpu 服务器看下 nvidia-smi", {"remote"}),
    ("连上 10.0.0.12 看看磁盘", {"remote"}),
    ("把这次改动 git commit 一下", {"git"}),
    ("打开桌面上的 chrome 看 ETH 周线", {"desktop"}),
])
def test_detect_user_triggers(msg, want):
    assert want <= tg.detect_user(msg)


@pytest.mark.parametrize("msg", [
    "写一个 python 脚本, 打印 100 以内的所有素数并运行验证",
    "请帮我写一份《2026年第三季度市场部工作总结》Word 文档，包含重点项目表格",
    "orders.csv 按地区汇总订单数和销售额输出到 Excel",
    "把 en_source.md 翻译成通顺、专业的简体中文",
])
def test_office_tasks_stay_core_only(msg):
    assert tg.detect_user(msg) == set()


def test_context_block_is_not_a_signal():
    """自动匹配的技能正文 (挂在 <context> 里) 常顺带提到无关工具, 不能据此加组; 用户原话照常算。"""
    ctx_text = ("<context>\n技能正文: 先 windows_nodes list, 再 ssh_exec; docker 服务器 git 定时 等泛词\n</context>\n\n"
                "写个素数脚本")
    assert tg.detect_messages([{"role": "user", "content": ctx_text}]) == set()
    ctx_text2 = "<context>\n无关\n</context>\n\n在 Windows 电脑上打开记事本"
    assert tg.detect_messages([{"role": "user", "content": ctx_text2}]) == {"windows"}


@pytest.mark.parametrize("msg", ["看下 diff.md 的内容", "ad_report.docx 写报告", "合并成 merged.xlsx", "每月销售额趋势",
                                 "查看主机名和内存", "写个 http server 在 localhost:8080 测一下"])
def test_no_false_positive_on_filenames_and_data_words(msg):
    assert tg.detect_user(msg) == set()


def test_system_message_ignored():
    sysmsg = {"role": "system", "content": "ssh_list_hosts windows_nodes project_init git_status 定时 ssh"}
    assert tg.detect_messages([sysmsg]) == set()


def test_core_always_and_stable_within_session(sess):
    msgs = [{"role": "system", "content": "提示 ssh_exec"}, {"role": "user", "content": "写个素数脚本"}]
    ctx = _ctx(sess, msgs)
    first = tg.tools_for(ctx, TOOL_DEFS)
    assert CORE_MUST <= set(_names(first)) and not (set(_names(first)) & set(tg.TOOL_TO_GROUP))
    msgs.append({"role": "assistant", "content": "好的"})
    again = tg.tools_for(ctx, TOOL_DEFS)
    assert again is first                         # 同一组合同一对象, 字节完全一致


def test_trigger_appends_at_tail_and_sticks(sess):
    msgs = [{"role": "user", "content": "写个素数脚本"}]
    ctx = _ctx(sess, msgs)
    before = _names(tg.tools_for(ctx, TOOL_DEFS))
    msgs.append({"role": "user", "content": "再帮我建个每天 9 点的定时提醒"})
    after = _names(tg.tools_for(ctx, TOOL_DEFS))
    assert after[:len(before)] == before          # 前缀不动, 新组只加在尾部
    assert {"create_timer", "list_timers"} <= set(after)
    # 历史被裁掉 / 新回合重新建 ctx: 组仍在 (只增不减)
    ctx2 = _ctx(sess, [{"role": "user", "content": "谢谢"}])
    assert _names(tg.tools_for(ctx2, TOOL_DEFS)) == after
    # 持久化: 内存丢了从会话目录恢复
    tg.forget(sess)
    assert _names(tg.tools_for(_ctx(sess, []), TOOL_DEFS)) == after
    assert json.loads((cfg.SESSIONS_DISK / sess / ".tool_groups.json").read_text()) == ["workflow"]


def test_guard_text_and_direct_call_activate(sess):
    msgs = [{"role": "user", "content": "写个小服务"},
            {"role": "tool", "tool_call_id": "1", "content": "[SYSTEM-AUTO-MAP] 请调用 update_map 工具填充"}]
    ctx = _ctx(sess, msgs)
    assert "update_map" in _names(tg.tools_for(ctx, TOOL_DEFS))
    msgs.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "2", "type": "function", "function": {"name": "ssh_list_hosts", "arguments": "{}"}}]})
    assert "ssh_exec" in _names(tg.tools_for(ctx, TOOL_DEFS))


def test_enable_tools_handler(sess):
    from core.tools.handlers import REGISTRY
    out, _ = asyncio.run(REGISTRY["enable_tools"](sess, {"groups": ["windows", "git_status"]}))
    assert "windows_desktop" in out and "git_status" in out
    names = _names(tg.tools_for(_ctx(sess, []), TOOL_DEFS))
    assert {"windows_desktop", "windows_system", "git_commit"} <= set(names)
    bad, _ = asyncio.run(REGISTRY["enable_tools"](sess, {"groups": ["nope"]}))
    assert bad.startswith("ERROR")


def test_enable_tools_call_in_history_restores(sess):
    msgs = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "enable_tools", "arguments": '{"groups": ["research"]}'}}]}]
    assert "github_search" in _names(tg.tools_for(_ctx(sess, msgs), TOOL_DEFS))


def test_disabled_config_sends_everything(sess, monkeypatch):
    monkeypatch.setitem(cfg.CFG, "agent", {**(cfg.CFG.get("agent") or {}), "tool_groups": False})
    assert tg.tools_for(_ctx(sess, []), TOOL_DEFS) is TOOL_DEFS
    monkeypatch.setitem(cfg.CFG, "agent", {**(cfg.CFG.get("agent") or {}), "tool_groups": {"always": ["git"]}})
    assert "git_status" in _names(tg.tools_for(_ctx(sess, []), TOOL_DEFS))


def test_no_session_uses_ctx_attr(monkeypatch):
    monkeypatch.setattr(tg, "_aliases", lambda: {})
    ctx = SimpleNamespace(session_id=None, messages=[{"role": "user", "content": "ssh 上去看看"}])
    assert "ssh_exec" in _names(tg.tools_for(ctx, TOOL_DEFS))
    ctx.messages = []
    assert "ssh_exec" in _names(tg.tools_for(ctx, TOOL_DEFS))


def test_agent_stream_sends_core_then_grows(monkeypatch):
    """真实 agent_stream(): 首轮只发核心工具; 工具结果里出现 update_map 后, 下一次调用模型时它就在列表里。"""
    import litecode_server as srv
    import lib.agent.llm_call as _llm_call
    import lib.agent.tool_exec as _tool_exec
    monkeypatch.setattr(tg, "_aliases", lambda: {})
    sent = []

    async def fake_llm(messages, tools, max_tokens_override=None, **_kw):
        sent.append(_names(tools))
        if len(sent) == 1:
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c0", "function": {
                "name": "execute_shell", "arguments": json.dumps({"command": "echo hi"})}}]}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        else:
            yield {"choices": [{"delta": {"content": "完成"}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    async def fake_tool(name, args, sid=None):
        return "hi\n[SYSTEM-AUTO-MAP] 请为已写入的源文件调用 update_map 工具", None

    monkeypatch.setattr(srv, "_vllm_stream", fake_llm)
    monkeypatch.setattr(_llm_call, "_vllm_stream", fake_llm)
    monkeypatch.setattr(srv, "execute_tool", fake_tool)
    monkeypatch.setattr(_tool_exec, "execute_tool", fake_tool)
    monkeypatch.setattr(srv, "_turn_persist", lambda *a, **k: None)

    async def _run():
        return "".join([c async for c in srv.agent_stream("写一个 python 脚本打印 100 以内素数并运行", None)])
    asyncio.run(_run())
    assert len(sent) >= 2
    assert "execute_shell" in sent[0] and "update_map" not in sent[0] and "windows_desktop" not in sent[0]
    assert "update_map" in sent[1] and sent[1][:len(sent[0])] == sent[0]


def test_skills_index_line_short():
    from lib.skills import index_line
    assert index_line("Python 数据分析技能。使用 pandas/numpy 做清洗与可视化", 72) == "Python 数据分析技能"
    long = "x" * 200
    assert index_line(long, 72) == "x" * 72 + "…"
    assert index_line(long, 0) == long
