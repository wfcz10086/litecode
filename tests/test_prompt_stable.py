"""system prompt 逐轮字节不变: 动态块移出 + session 上下文按会话冻结 + 占位符只注入一次。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))

import pytest  # noqa: E402

import lib.prompt as P  # noqa: E402
import lib.prompt_snapshot as snap  # noqa: E402
import lib.turn_context as tc  # noqa: E402
import lib.turn_sources as ts  # noqa: E402

# 第一条命中"数据获取+文档输出"委派规则, 第二条什么都不命中
MSG_DELEGATE = "帮我搜索下 btc 现在行情 生成报告"
MSG_PLAIN = "好的谢谢"
SID = "t-stable"


@pytest.fixture
def sess(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "SESSIONS_DISK", tmp_path)
    monkeypatch.setattr(snap, "_mem", {})
    d = tmp_path / SID
    d.mkdir()
    (d / "USER.md").write_text("# USER\n- **Name:** 小白\n")
    return d


def test_dynamic_blocks_leave_system_prompt(sess):
    a = P.build_request_system_prompt(MSG_DELEGATE, SID, history_anchor="h", include_dynamic=False)
    b = P.build_request_system_prompt(MSG_PLAIN, SID, history_anchor="h", include_dynamic=False)
    assert a == b
    assert "[MANDATORY]" not in a


def test_old_behaviour_still_available(sess):
    a = P.build_request_system_prompt(MSG_DELEGATE, SID)
    assert a.startswith("[MANDATORY]")


def test_delegation_moves_to_turn_context(sess):
    ctx = tc.build(tc.TurnInput(MSG_DELEGATE, SID, []))
    assert ctx.startswith("<context>") and "[MANDATORY]" in ctx
    assert "[MANDATORY]" not in tc.build(tc.TurnInput(MSG_PLAIN, SID, []))


def test_session_context_frozen_until_history_head_changes(sess):
    first = P.build_request_system_prompt(MSG_PLAIN, SID, history_anchor="h1", include_dynamic=False)
    (sess / "USER.md").write_text("# USER\n- **Name:** 阿鹈\n")      # 轮末自动学习改了 USER.md
    same = P.build_request_system_prompt(MSG_DELEGATE, SID, history_anchor="h1", include_dynamic=False)
    assert same == first and "小白" in same
    snap._mem.clear()                                               # 模拟服务重启: 读盘上快照
    assert P.build_request_system_prompt(MSG_PLAIN, SID, history_anchor="h1", include_dynamic=False) == first
    fresh = P.build_request_system_prompt(MSG_PLAIN, SID, history_anchor="h2", include_dynamic=False)
    assert "阿鹈" in fresh


def test_freeze_can_be_disabled(sess, monkeypatch):
    monkeypatch.setattr(P, "CFG", {"agent": {"freeze_session_context": False}})
    P.build_request_system_prompt(MSG_PLAIN, SID, history_anchor="h1", include_dynamic=False)
    (sess / "USER.md").write_text("# USER\n- **Name:** 阿鹈\n")
    assert "阿鹈" in P.build_request_system_prompt(MSG_PLAIN, SID, history_anchor="h1", include_dynamic=False)


def test_session_context_injected_once(sess):
    if not P.PROMPT_FILE.exists():
        pytest.skip("no AGENT_PROMPT.md")
    s = P.build_request_system_prompt(MSG_PLAIN, SID, history_anchor="h", include_dynamic=False)
    assert s.count("**Name:** 小白") == 1


def test_anchor_first_turn_equals_second_turn():
    """第一轮 (空历史) 与第二轮 (历史首条=第一轮原话) 锚点一致, 快照不会在第二轮被刷新。"""
    a1 = snap.history_anchor([], "你好")
    stored = {"role": "user", "content": "你好", tc.KEY: "<context>x</context>"}
    a2 = snap.history_anchor([stored, {"role": "assistant", "content": "hi"}], "下一句")
    assert a1 == a2


def test_prepare_returns_sendable_history(sess, monkeypatch):
    monkeypatch.setattr(ts, "CFG", {"agent": {"memory_recall": False}})
    raw = [{"role": "user", "content": "你好", tc.KEY: "<context>\nC\n</context>"},
           {"role": "assistant", "content": "hi"}]
    anchor, ctx, hist = ts.prepare(MSG_DELEGATE, SID, raw)
    assert anchor == snap.history_anchor([], "你好")
    assert "[MANDATORY]" in ctx
    assert hist[0]["content"].startswith("<context>") and tc.KEY not in hist[0]


def test_skills_not_repeated_once_in_history(monkeypatch):
    block = "## 已自动加载的匹配 Skills\n[Active Skill: a]\nAAA\n[Active Skill: b]\nBBB\n"
    monkeypatch.setattr(ts, "matched_skills_block", lambda msg, external=True: block)
    first = ts.skills(tc.TurnInput("x", SID, []))
    assert first == block
    hist = [{"role": "user", "content": "x", tc.KEY: "[Active Skill: a]\nAAA\n"}]
    assert ts.skills(tc.TurnInput("y", SID, hist)) == "## 已自动加载的匹配 Skills\n[Active Skill: b]\nBBB\n"
    hist.append({"role": "user", "content": "y", tc.KEY: "[Active Skill: b]\nBBB\n"})
    assert ts.skills(tc.TurnInput("z", SID, hist)) == ""
