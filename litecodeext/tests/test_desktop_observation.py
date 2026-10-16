"""桌面画面直接进入模型输入，工具响应链完整，旧图片不会无限累积。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.desktop_observation import append_latest_observation, observation_message


def test_latest_real_image_is_model_input_and_not_persisted(tmp_path):
    from PIL import Image
    path = tmp_path / 'uploads/screen.png'
    path.parent.mkdir()
    Image.new('RGB', (2560, 1600), 'white').save(path)
    result = json.dumps({'ok': True, 'alias': 'win11', 'image': str(path), 'state': 'clicked'})
    observation = observation_message(result, tmp_path)
    assert '2560x1600' in observation['content'][0]['text']
    assert '1600x1000' in observation['content'][0]['text']
    assert observation['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    calls = [{'id': 'one', 'function': {'name': 'windows_desktop'}}]
    tools = [{'role': 'tool', 'tool_call_id': 'one', 'content': result}]
    persisted = list(tools)
    user_image = {'role': 'user', 'content': [{'type': 'text', 'text': '用户上传的截图'},
                   {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,user'}}]}
    messages = [user_image, {'role': 'assistant', 'tool_calls': calls}, *tools]
    assert append_latest_observation(messages, calls, tools, tmp_path)
    assert messages[-2]['role'] == 'tool' and messages[-1]['role'] == 'user'
    assert append_latest_observation(messages, calls, tools, tmp_path)
    assert sum(isinstance(m.get('content'), list) for m in messages) == 2
    assert user_image['content'][1]['image_url']['url'].endswith('user')
    assert not any(isinstance(m.get('content'), list) for m in persisted)


def test_missing_or_external_image_never_fabricates_observation(tmp_path):
    assert observation_message('ERROR: capture failed', tmp_path) is None
    assert observation_message('{"image":"/etc/passwd"}', tmp_path) is None
