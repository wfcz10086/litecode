"""桌面过程事件不包含凭据，支持错误、去重与聊天回放收集。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.desktop_steps import desktop_step


def test_event_contains_artifacts_but_no_input_credentials(tmp_path):
    image = str(tmp_path / 'uploads/a.png')
    step = desktop_step(json.dumps({'ok': True, 'state': 'clicked', 'image': image}),
                        {'alias': 'win11', 'action': 'type_secret', 'text': 'private', 'password': 'private'},
                        tmp_path, 'step-1')
    assert step['image'] == image and step['ok'] is True
    assert 'private' not in json.dumps(step)
    bad = desktop_step('{"ok": true,"image":"/etc/passwd"}', {}, tmp_path, 'step-2')
    assert 'image' not in bad


def test_error_event_and_collector_replay_dedup(tmp_path):
    from routers.chat_router import _collect
    step = desktop_step('ERROR: {"ok":false,"state":"desktop_unavailable","error":"桌面已锁定"}',
                        {'alias': 'win11', 'action': 'ocr'}, tmp_path, 'step-3')
    assert not step['ok'] and step['error'] == '桌面已锁定'
    collected = {'text': '', 'reasoning': '', 'tools': [], 'diffs': [], 'usage': None}
    frame = 'data: ' + json.dumps({'choices': [{'delta': {'desktop_step': step}}]})
    _collect(collected, frame)
    _collect(collected, frame)
    assert collected['desktop_steps'] == [step]
    assert desktop_step(None, {}, tmp_path, 'bad')['state'] == 'error'
