"""Unit tests for _filter_system_leak in wechat_bridge — P0-1.

litecode_server.py injects internal control messages like [系统: ...] /
[SYSTEM-XXX] ... into the content stream (reasoning-stall interrupt, loop
detection, force-action nudges). These are meant for the model, not real
WeChat users. wechat_bridge._call_agent_stream must strip them before the
text reaches session persistence or the outgoing WeChat message.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))

from wechat_bridge import _filter_system_leak


def test_reasoning_stall_message_stripped():
    text = "前半段正常回复\n[系统: 推理超过 7000 字仍未产出 (第 1 次超限, 已中断本轮)]\n后半段正常回复"
    cleaned = _filter_system_leak(text)
    assert "[系统:" not in cleaned
    assert "推理超过" not in cleaned
    assert "前半段正常回复" in cleaned
    assert "后半段正常回复" in cleaned


def test_system_emergency_tag_and_trailing_text_stripped():
    text = "开始\n[SYSTEM-EMERGENCY] 你已连续 3 次空回, 用户在等.\n结束"
    cleaned = _filter_system_leak(text)
    assert "[SYSTEM-EMERGENCY]" not in cleaned
    assert "你已连续" not in cleaned
    assert "开始" in cleaned
    assert "结束" in cleaned


def test_system_override_stripped():
    text = "答案是 42\n[SYSTEM OVERRIDE] 检测到死循环, 已清除重复结果。"
    cleaned = _filter_system_leak(text)
    assert "SYSTEM OVERRIDE" not in cleaned
    assert "答案是 42" in cleaned


def test_normal_text_with_brackets_unaffected():
    """普通正文里的方括号 (不是系统消息) 不该被误删."""
    text = "参考文档 [1] 和配置项 [debug=true] 都正常, 结论: 通过"
    cleaned = _filter_system_leak(text)
    assert cleaned == text


def test_only_system_message_yields_empty():
    text = "[系统: 推理超过 7000 字仍未产出 (第 1 次超限, 已中断本轮)]"
    cleaned = _filter_system_leak(text)
    assert cleaned == ""


def test_empty_and_none_safe():
    assert _filter_system_leak("") == ""
    assert _filter_system_leak(None) is None


def test_multiple_leaks_in_one_message_all_stripped():
    text = (
        "step1\n[系统] 上一轮你陷入了推理复述循环 (没有 emit 任何 tool_call / content).\n"
        "step2\n[SYSTEM-FORCE-ACTION] 你上一轮没有产出 tool_call 或正文回复, 用户在等.\n"
        "step3"
    )
    cleaned = _filter_system_leak(text)
    assert "[系统]" not in cleaned
    assert "[SYSTEM-FORCE-ACTION]" not in cleaned
    assert "step1" in cleaned and "step2" in cleaned and "step3" in cleaned
