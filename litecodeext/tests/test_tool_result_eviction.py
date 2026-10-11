"""旧工具结果淘汰: 批次推进 + 不改原消息 + 多轮前缀稳定。"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.tool_result_eviction import (  # noqa: E402
    clear_old_tool_results, eviction_count, strip_old_reasoning,
)


def _turn(i: int, size: int = 1000) -> list:
    return [
        {"role": "user", "content": f"q{i}"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": f"{i}:" + "x" * size},
    ]


def test_count_moves_in_whole_batches():
    assert eviction_count([1000] * 4, 4, 8, 10**9) == 0
    assert eviction_count([1000] * 11, 4, 8, 10**9) == 0
    assert eviction_count([1000] * 12, 4, 8, 10**9) == 8
    assert eviction_count([1000] * 19, 4, 8, 10**9) == 8
    assert eviction_count([1000] * 20, 4, 8, 10**9) == 16


def test_budget_also_moves_in_batches_and_can_clear_all():
    assert eviction_count([1000] * 10, 4, 8, 5000) == 8
    assert eviction_count([10000] * 3, 4, 8, 5000) == 3


def test_does_not_mutate_input():
    msgs = sum((_turn(i) for i in range(14)), [])
    snap = copy.deepcopy(msgs)
    out = clear_old_tool_results(msgs, keep_recent=4)
    assert msgs == snap
    cleared = [m for m in out if m.get("role") == "tool" and m["content"].startswith("[cleared:")]
    assert len(cleared) == 8
    assert cleared[0]["content"].startswith("[cleared:read_file 1002c] 0:")


def test_short_results_kept_verbatim():
    msgs = sum((_turn(i, size=50) for i in range(14)), [])
    out = clear_old_tool_results(msgs, keep_recent=4)
    assert out == msgs


def test_prefix_stable_across_turns():
    """逐轮追加, 统计"上一轮发出的历史"在下一轮里被改写的次数。旧实现每轮都改。"""
    history, prev, rewrites = [], None, 0
    for i in range(30):
        history += _turn(i)
        sent = clear_old_tool_results(history, keep_recent=4, max_total_chars=10**9)
        if prev is not None and json.dumps(sent[:len(prev)]) != json.dumps(prev):
            rewrites += 1
        prev = sent
    assert rewrites <= 30 // 8 + 1


def test_strip_old_reasoning_copies():
    msgs = [{"role": "assistant", "content": "a", "reasoning_content": "r1"},
            {"role": "assistant", "content": "b", "reasoning_content": "r2"}]
    out = strip_old_reasoning(msgs, keep=1)
    assert [m["reasoning_content"] for m in out] == ["", "r2"]
    assert msgs[0]["reasoning_content"] == "r1"
    assert "reasoning_content" in out[0]
