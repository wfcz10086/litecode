"""关思考那一轮附加"别把分析过程写进正文"的说明: 只对这次请求生效, 不写进对话历史 (2026-10-22 真机泄漏)。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import litecode_server  # noqa: E402,F401  (初始化扁平导入路径)
import lib.agent.llm_call as L  # noqa: E402
from lib.agent_state import StreamResult, TurnContext, UsageCounters  # noqa: E402


def _run(ctx, monkeypatch):
    seen = []

    async def fake(messages, tools, max_tokens_override=None, **kw):
        seen.append((list(messages), kw))
        yield {"choices": [{"delta": {"content": "好"}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    monkeypatch.setattr(L, "_vllm_stream", fake)

    async def go():
        out = StreamResult()
        async for _ in L._stream_llm_call(ctx, UsageCounters(), "m", 0.0, 0, out):
            pass
    asyncio.run(go())
    return seen


def test_no_think_turn_gets_note_but_history_untouched(monkeypatch):
    ctx = TurnContext(sid_tag="t", session_id=None)
    ctx.messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "点开那个视频"}]
    ctx.no_think_next = True
    seen = _run(ctx, monkeypatch)
    msgs, kw = seen[0]
    assert kw.get("no_thinking") is True
    assert msgs[-1]["role"] == "user" and L.NO_THINK_NOTE in msgs[-1]["content"]   # system 只能在开头
    assert [m["role"] for m in msgs].count("system") == 1
    assert all(L.NO_THINK_NOTE not in str(m.get("content")) for m in ctx.messages)   # 不进历史
    assert ctx.no_think_next is False


def test_normal_turn_has_no_note(monkeypatch):
    ctx = TurnContext(sid_tag="t", session_id=None)
    ctx.messages = [{"role": "user", "content": "你好"}]
    seen = _run(ctx, monkeypatch)
    msgs, kw = seen[0]
    assert "no_thinking" not in kw and all(L.NO_THINK_NOTE not in str(m.get("content")) for m in msgs)


def test_plan_gate_skipped_when_recently_planned():
    """上一轮拆过任务 → 追加要求那一轮不再催拆解 (2026-10-22 D2 多跑 8 次记账工具)。"""
    import tools.handlers.task_ops as T
    T._STORE.pop("web-plan-t", None)
    assert not T.recently_planned("web-plan-t")
    asyncio.run(T.h_task_create("web-plan-t", {"title": "下载视频"}))
    assert T.recently_planned("web-plan-t") and not T.recently_planned("web-other")
    T._STORE.pop("web-plan-t", None)
