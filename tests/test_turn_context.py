"""lib/turn_context: 来源插拔 / 渲染 / 挂到 user 消息 / 历史拼回与剥离。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))

import pytest  # noqa: E402

import lib.turn_context as tc  # noqa: E402


@pytest.fixture
def clean_sources(monkeypatch):
    monkeypatch.setattr(tc, "_SOURCES", {})


def test_sources_run_in_order_and_skip_empty(clean_sources):
    tc.register("b", lambda i: "B:" + i.user_message, order=20)
    tc.register("a", lambda i: "A", order=10)
    tc.register("empty", lambda i: "  ", order=5)
    assert tc.collect(tc.TurnInput("hi")) == [("a", "A"), ("b", "B:hi")]


def test_failing_source_isolated(clean_sources):
    tc.register("boom", lambda i: 1 / 0, order=1)
    tc.register("ok", lambda i: "fine", order=2)
    assert tc.collect(tc.TurnInput("x")) == [("ok", "fine")]


def test_unregister_and_reregister_overrides(clean_sources):
    tc.register("a", lambda i: "old")
    tc.register("a", lambda i: "new")
    assert tc.build(tc.TurnInput("x")).count("new") == 1
    tc.unregister("a")
    assert tc.build(tc.TurnInput("x")) == ""


def test_attach_string_and_multimodal():
    assert tc.attach("q", "") == "q"
    assert tc.attach("q", "<context>c</context>") == "<context>c</context>\n\nq"
    parts = [{"type": "image_url", "image_url": {"url": "x"}}]
    out = tc.attach(parts, "C")
    assert out[0] == {"type": "text", "text": "C"} and out[1:] == parts


def test_materialize_roundtrip_matches_what_was_sent():
    ctx = tc.render([("s", "记忆一条")])
    sent = tc.attach("原话", ctx)
    stored = {"role": "user", "content": "原话", tc.KEY: ctx}
    hist = [stored, {"role": "assistant", "content": "好"}]
    out = tc.materialize(hist)
    assert out[0] == {"role": "user", "content": sent}      # 下一轮与本轮发送逐字一致
    assert tc.KEY in stored and stored["content"] == "原话"  # 不改原 dict
    assert out[1] is hist[1]


def test_strip_restores_user_words():
    ctx = tc.render([("s", "必须使用 spawn_agent")])
    hist = tc.materialize([{"role": "user", "content": "帮我写", tc.KEY: ctx}])
    assert tc.strip(hist)[0]["content"] == "帮我写"
    mm = tc.strip([{"role": "user", "content": [{"type": "text", "text": ctx}, {"type": "text", "text": "q"}]}])
    assert mm[0]["content"] == [{"type": "text", "text": "q"}]
    plain = [{"role": "user", "content": "<context> 用户自己打的"}]
    assert tc.strip(plain) == plain


def test_seen_in_collects_previous_context():
    hist = [{"role": "user", "content": "a", tc.KEY: "X1"}, {"role": "user", "content": "b"},
            {"role": "user", "content": "c", tc.KEY: "X2"}]
    assert tc.seen_in(hist) == "X1\nX2"
