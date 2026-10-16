"""图片产出物混入 diff 时，SSE 收集不能把异常对象继续写回历史。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]


def test_collect_ignores_artifact_in_diff_but_keeps_patch():
    import json
    from routers.chat_router import _collect
    collected = {'text': '', 'reasoning': '', 'tools': [], 'diffs': [], 'usage': None}
    for value in ({'filepath': '/tmp/a.png', 'op': 'screenshot'}, ['oops'], 1, True, None):
        _collect(collected, 'data: ' + json.dumps({'choices': [{'delta': {
            'diff_view': {'filepath': '/tmp/a', 'diff': value}}}]}))
    assert collected['diffs'] == []
    _collect(collected, 'data: ' + json.dumps({'choices': [{'delta': {
        'diff_view': {'filepath': '/tmp/a', 'diff': '+new\n-old'}}}]}))
    assert collected['diffs'] == [{'filepath': '/tmp/a', 'diff': '+new\n-old'}]
