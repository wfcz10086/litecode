"""上游不认 reasoning_effort 档位 (400) 时就地改档重发, 不让整轮中断。

2026-10-19 生产事故: 自建 Flash 换了新模板, 只认 xhigh/medium/low;
LiteCode 思考预算 >8000 时发 high → 400 "Unexpected reasoning effort high" → 用户那轮直接报错。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.transport import _fix_effort  # noqa: E402

MSG = (b'{"error":{"message":"Unexpected reasoning effort high. Supported types are '
       b'xhigh (default), medium, and low.","type":"BadRequestError","param":null,"code":400}}')


def test_high_maps_to_xhigh():
    body = {"reasoning_effort": "high"}
    assert _fix_effort(body, MSG)
    assert body["reasoning_effort"] == "xhigh"


def test_falls_back_to_medium_when_no_xhigh():
    body = {"reasoning_effort": "high"}
    assert _fix_effort(body, b"Unexpected reasoning effort high. Supported types are medium and low.")
    assert body["reasoning_effort"] == "medium"


def test_drops_field_when_no_list():
    body = {"reasoning_effort": "high"}
    assert _fix_effort(body, "invalid reasoning effort")
    assert "reasoning_effort" not in body


def test_unrelated_400_untouched():
    body = {"reasoning_effort": "high"}
    assert not _fix_effort(body, b'{"error":{"message":"maximum context length exceeded"}}')
    assert body["reasoning_effort"] == "high"
    assert not _fix_effort({}, MSG)      # 本来就没发 effort, 不是我们的问题
