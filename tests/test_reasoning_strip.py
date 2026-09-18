"""Unit tests for _strip_tool_call_literals in litecode_server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))

from litecode_server import _strip_tool_call_literals


def test_single_tool_call_stripped():
    text = "thinking about it\n<tool_call>\n<function=web_fetch>\n<parameter=url>https://x.com</parameter>\n</function>\n</tool_call>\ndone"
    cleaned, stripped = _strip_tool_call_literals(text)
    assert "<tool_call>" not in cleaned
    assert "</tool_call>" not in cleaned
    assert "thinking about it" in cleaned
    assert "done" in cleaned
    assert "<tool_call>" in stripped


def test_isolated_function_block_stripped():
    text = "let me check<function=foo><parameter=x>v</parameter></function>result"
    cleaned, stripped = _strip_tool_call_literals(text)
    assert "<function=" not in cleaned
    assert "</function>" not in cleaned
    assert "let me check" in cleaned
    assert "result" in cleaned
    assert "<function=foo>" in stripped


def test_normal_reasoning_unaffected():
    text = "这是正常推理内容\n```python\nprint('hello')\n```\n没有工具调用"
    cleaned, stripped = _strip_tool_call_literals(text)
    assert cleaned == text
    assert stripped == ""


def test_multiple_blocks_stripped():
    text = (
        "step 1\n"
        "<tool_call><function=search><parameter=q>foo</parameter></function></tool_call>\n"
        "step 2\n"
        "<function=bar><parameter=x>1</parameter></function>\n"
        "step 3"
    )
    cleaned, stripped = _strip_tool_call_literals(text)
    assert "<tool_call>" not in cleaned
    assert "<function=" not in cleaned
    assert "step 1" in cleaned
    assert "step 2" in cleaned
    assert "step 3" in cleaned
    assert len(stripped) > 0
