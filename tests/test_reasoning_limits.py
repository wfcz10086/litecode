"""lib/reasoning_limits.stall_limits: 熔断线跟随思考预算, 有下限, 可配置覆盖。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))

import lib.reasoning_limits as rl  # noqa: E402


def _set(monkeypatch, thinking, budget, override=None):
    monkeypatch.setattr(rl, "_LIVE", {"enable_thinking": thinking, "thinking_budget": budget})
    monkeypatch.setattr(rl, "CFG", {"agent": {"reasoning_stall_break_chars": override} if override else {}})


def test_follows_thinking_budget(monkeypatch):
    _set(monkeypatch, True, 4096)
    assert rl.stall_limits() == (4096 * 3 * 5 // 7, 4096 * 3)


def test_floor_for_small_budget(monkeypatch):
    _set(monkeypatch, True, 1000)
    assert rl.stall_limits()[1] == 7000


def test_thinking_off_uses_floor(monkeypatch):
    _set(monkeypatch, False, 8192)
    assert rl.stall_limits() == (5000, 7000)


def test_config_override(monkeypatch):
    _set(monkeypatch, True, 4096, override=20000)
    assert rl.stall_limits() == (20000 * 5 // 7, 20000)


def _stream(text, size=3):
    return [text[i:i + size] for i in range(0, len(text), size)]


def test_detector_ignores_token_fragments_of_code():
    """实测误报: 模型在推理里写 SMIL values="...;...;...", token 级片段被当成行。"""
    det = rl.RepeatLineDetector()
    text = 'Leg animation: use <animate attributeName="d" dur="1.2s" values="' + "...;" * 40 + '"/>.\n'
    assert not any(det.feed(c) for c in _stream(text, 2))


def test_detector_catches_real_repetition():
    det = rl.RepeatLineDetector()
    text = "让我先搜索一下。\n我先查一下文件。\n" * 5
    assert any(det.feed(c) for c in _stream(text))
    assert set(det.recent()) == {"让我先搜索一下。", "我先查一下文件。"}


def test_detector_normal_design_notes_pass():
    det = rl.RepeatLineDetector()
    notes = "\n".join(f"Step {i}: place element {i} at ({i * 10}, {i * 7})." for i in range(30)) + "\n"
    assert not any(det.feed(c) for c in _stream(notes, 4))


def test_detector_reset_clears_state():
    det = rl.RepeatLineDetector()
    for c in _stream("同一句\n" * 5):
        det.feed(c)
    det.reset()
    assert not det.feed("同一句\n")


def test_salvage_empty():
    assert rl.salvage_note("") == "" and rl.salvage_note("   ") == ""


def test_salvage_short_kept_whole():
    note = rl.salvage_note("曲柄中心 (380, 385), 半径 38")
    assert "曲柄中心 (380, 385), 半径 38" in note and "中间省略" not in note and "不要从头重新推导" in note


def test_salvage_long_keeps_head_and_tail():
    r = "方案总览。" + "x" * 10000 + "最后决定: 腿用 SMIL 驱动。"
    note = rl.salvage_note(r, head=50, tail=60)
    assert note.count("方案总览。") == 1 and "腿用 SMIL 驱动" in note and "中间省略" in note
    assert len(note) < 400
