"""桌面接入契约：状态识别、错误返回、图片产出与工具定义。"""
import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.windows_desktop_worker import classify
from lib.tools.defs.windows_desktop_defs import WINDOWS_DESKTOP_DEFS


@pytest.mark.parametrize('action', ('screenshot', 'click', 'ocr', 'type_secret', 'hotkey', 'type_text', 'open_url', 'launch_app'))
def test_locked_desktop_refuses_gui_actions_before_side_effects(tmp_path, action):
    from lib import windows_desktop_worker as worker

    def forbidden(*args, **kwargs):
        raise AssertionError('锁屏时不得执行桌面动作')

    desktop = types.SimpleNamespace(perform=forbidden)
    with patch.object(worker, 'desktop_ready', return_value=False), \
         patch.dict(sys.modules, {'windows_desktop_actions': desktop, 'windows_desktop_ocr': desktop}):
        result = worker.run(action, tmp_path, {'text': 'test', 'keys': 'enter'})
    assert result['ok'] is False and result['state'] == 'desktop_unavailable'
    assert 'image' not in result


@pytest.mark.parametrize('action', ('system_info', 'processes', 'services', 'run_powershell', 'list_dir', 'download', 'upload'))
def test_background_actions_do_not_require_unlocked_desktop(tmp_path, action):
    from lib import windows_desktop_worker as worker
    calls = []

    def perform(actual, folder, args):
        calls.append(actual)
        return {'ok': True, 'state': actual}

    backend = types.SimpleNamespace(perform=perform)
    with patch.object(worker, 'desktop_ready', side_effect=AssertionError('后台操作不应依赖桌面状态')), \
         patch.dict(sys.modules, {'windows_system_actions': backend, 'windows_file_actions': backend}):
        result = worker.run(action, tmp_path, {})
    assert result['ok'] is True and calls == [action]


def test_login_states_require_evidence():
    assert classify(['微信']) == 'unknown'
    assert classify(['微信', '扫码登录']) == 'awaiting_scan'
    assert classify(['请在手机上确认登录']) == 'awaiting_confirmation'
    assert classify(['二维码已过期', '扫码登录']) == 'qr_expired'
    assert classify(['通讯录', '聊天']) == 'logged_in'


def test_invalid_action_never_connects():
    from lib.windows_desktop import execute
    with patch('core.tools.handlers.remote._connect') as connect:
        try:
            execute({'action': 'delete'}, '/tmp')
        except ValueError:
            pass
        else:
            raise AssertionError('非法操作必须拒绝')
        connect.assert_not_called()


def test_handler_returns_image_marker_and_artifact():
    from core.tools.handlers.windows_desktop import h_windows_desktop
    with patch('lib.windows_desktop.execute', return_value={'ok': True, 'image': '/tmp/a.png'}), \
         patch('core.tool_dispatch._add_artifact') as emit:
        text, artifact = asyncio.run(h_windows_desktop('test', {'alias': 'win11'}))
    assert '[发送图片: /tmp/a.png]' in text
    assert artifact is None  # 第二返回位是 diff，不得把图片对象放进去。
    emit.assert_called_once()


def test_handler_failure_has_no_image():
    from core.tools.handlers.windows_desktop import h_windows_desktop
    with patch('lib.windows_desktop.execute', return_value={'ok': False, 'state': 'desktop_unavailable'}):
        text, artifact = asyncio.run(h_windows_desktop('test', {'alias': 'win11'}))
    assert 'desktop_unavailable' in text and artifact is None


def test_schema_and_plugin_share_contract():
    from plugins.tools.windows_desktop import SCHEMA
    assert SCHEMA == WINDOWS_DESKTOP_DEFS[0]['function']['parameters']
    assert SCHEMA['required'] == ['alias', 'action']


def test_image_prefix_in_long_history_does_not_recurse():
    from lib.config import est_tokens, IMAGE_TOKEN_COST
    # 对话中代码片段含空 data URL 前缀，曾在上下文估算时无限递归。
    text = 'x' * 5000 + 'data:image/png;base64,'
    assert est_tokens(text) == est_tokens('x' * 5000) + IMAGE_TOKEN_COST
    assert est_tokens('data:image/png;base64,' + 'A' * 6000) == IMAGE_TOKEN_COST


def test_ocr_unions_word_boxes_and_rejects_duplicate_targets():
    from lib.windows_desktop_ocr import normalize_lines, select_text
    import pytest
    raw = {'lines': [{'text': '不 登录 继续', 'words': [
        {'bounding_rect': {'x': 10, 'y': 20, 'width': 20, 'height': 10}},
        {'bounding_rect': {'x': 35, 'y': 18, 'width': 40, 'height': 15}}]}]}
    lines = normalize_lines(raw)
    assert lines[0]['rect'] == {'x': 10, 'y': 18, 'width': 65, 'height': 15}
    assert normalize_lines(raw, scale=2)[0]['rect'] == {'x': 20, 'y': 36, 'width': 130, 'height': 30}
    _, x, y = select_text(lines, '不登录继续', 100, 100)
    assert (x, y) == (42.5, 25.5)
    with pytest.raises(ValueError, match='匹配 2 个'):
        select_text(lines * 2, '不登录继续', 100, 100)
    with pytest.raises(ValueError, match='匹配 0 个'):
        select_text(lines, '取消', 100, 100)   # [2026-10-19] '登录' 现按行内子串命中, 0 个改用真不存在的文字
    with pytest.raises(ValueError, match='屏幕内'):
        select_text(lines, '不登录继续', 10, 10)


def test_ocr_invalid_boxes_are_not_clickable():
    from lib.windows_desktop_ocr import normalize_lines
    assert normalize_lines({'lines': [{'text': '确定', 'words': [
        {'bounding_rect': {'x': None, 'y': 0, 'width': 2, 'height': 2}}]}]}) == []


def test_generic_desktop_rejects_ambiguous_window_and_non_web_url():
    from lib.windows_desktop_actions import choose_window, validate_url
    import pytest
    window = {'title': '安装 Chrome', 'handle': 1}
    assert choose_window([window], '安装 Chrome') == window
    with pytest.raises(ValueError):
        choose_window([window, window], '安装 Chrome')
    with pytest.raises(ValueError):
        choose_window([window], 'Chrome')
    assert validate_url('https://edu.51cto.com') == 'https://edu.51cto.com'
    for url in ('file:///C:/x', 'javascript:alert(1)', 'https://u:p@example.com'):
        with pytest.raises(ValueError):
            validate_url(url)


def test_duplicate_window_titles_can_be_selected_by_verified_handle():
    import pytest
    from lib.windows_desktop_actions import choose_window
    windows = [{'title': '微信', 'handle': 1}, {'title': '微信', 'handle': 2}]
    assert choose_window(windows, '微信', 2)['handle'] == 2
    assert choose_window(windows, '', 1)['handle'] == 1
    for title, handle in [('其他', 2), ('微信', 3), ('微信', True), ('微信', -1)]:
        with pytest.raises(ValueError):
            choose_window(windows, title, handle)


def test_credential_reference_is_resolved_only_for_secret_action(tmp_path):
    import json
    import pytest
    from lib.windows_desktop import desktop_request
    (tmp_path / '.secrets').mkdir()
    (tmp_path / '.secrets/windows_credentials.json').write_text(json.dumps({'test': {'password': 'private'}}))
    result = desktop_request({'action': 'type_secret', 'credential': 'test', 'field': 'password'}, tmp_path)
    assert result['text'] == 'private' and result['sensitive'] is True
    assert 'private' not in str(desktop_request({'action': 'screenshot'}, tmp_path))
    with pytest.raises(ValueError):
        desktop_request({'action': 'type_secret', 'credential': 'test', 'field': 'other'}, tmp_path)


def test_pointer_mapping_handles_scaled_image_and_rejects_changed_display():
    import pytest
    from lib.windows_desktop_actions import image_point
    monitor = {'width': 2560, 'height': 1600, 'left': 0, 'top': 0}
    args = {'screen_size': [2560, 1600], 'image_width': 1600, 'image_height': 1000,
            'x': 800, 'y': 500, 'end_x': 1500, 'end_y': 900}
    assert image_point(args, monitor) == (1280, 800)
    assert image_point(args, monitor, end=True) == (2400, 1440)
    for change in ({'x': 1600}, {'y': -1}, {'x': float('nan')}, {'image_height': 500}, {'screen_size': [1920, 1080]}):
        with pytest.raises(ValueError):
            image_point({**args, **change}, monitor)


def test_unicode_input_covers_chinese_and_surrogate_pairs():
    from lib.windows_desktop_actions import unicode_events
    events = unicode_events('A中😀')
    assert events == [(65, 4), (65, 6), (0x4e2d, 4), (0x4e2d, 6),
                      (0xd83d, 4), (0xd83d, 6), (0xde00, 4), (0xde00, 6)]
    assert unicode_events('') == []


def test_native_hotkeys_hold_modifiers_and_release_in_reverse():
    from lib.windows_desktop_actions import hotkey_events
    assert hotkey_events(['ctrl', 'a']) == [(17, 0, 0), (65, 0, 0), (65, 0, 2), (17, 0, 2)]
    assert hotkey_events(['alt', 'f4'])[1] == (0x73, 0, 0)
    assert hotkey_events(['ctrl', '+'])[:3] == [(17, 0, 0), (16, 0, 0), (0xbb, 0, 0)]


def test_keyboard_packets_include_scan_codes_and_preserve_unicode():
    from lib.windows_desktop_actions import keyboard_packet
    mapping = lambda vk, _: {65: 0x1e, 0x25: 0x4b}[vk]
    assert keyboard_packet(65, 0, 0, mapping) == (0, 0x1e, 8)
    assert keyboard_packet(65, 0, 2, mapping) == (0, 0x1e, 10)
    assert keyboard_packet(0x25, 0, 0, mapping) == (0, 0x4b, 9)
    assert keyboard_packet(0, 0x4e2d, 4, mapping) == (0, 0x4e2d, 4)


@pytest.mark.parametrize('keys,arrow,select', [
    ('ctrl+up', 0x48, False), ('ctrl+down', 0x50, False),
    ('ctrl+shift+up', 0x48, True), ('ctrl+shift+down', 0x50, True),
])
def test_paragraph_hotkeys_reach_native_input_without_shell_permission(tmp_path, monkeypatch, keys, arrow, select):
    import ctypes
    from lib import windows_desktop_actions as actions

    sent, screenshots = [], []
    scans = {0x11: 0x1d, 0x10: 0x2a, 0x26: 0x48, 0x28: 0x50}

    def send_input(count, pointer, size):
        packet = pointer._obj.payload.keyboard
        sent.append((packet.vk, packet.scan, packet.flags))
        return 1

    class Screen:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def shot(self, output):
            screenshots.append(output)

    user32 = types.SimpleNamespace(SetProcessDPIAware=lambda: None,
                                   MapVirtualKeyW=lambda vk, mode: scans[vk], SendInput=send_input)
    monkeypatch.setattr(ctypes, 'windll', types.SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(actions, 'pyautogui_module', lambda: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'mss', types.SimpleNamespace(MSS=Screen))
    monkeypatch.setattr(actions, 'list_windows', lambda: [])
    monkeypatch.setattr(actions.time, 'sleep', lambda seconds: None)

    result = actions.perform('hotkey', tmp_path, {'keys': keys, 'shell_allowed': False})

    pressed = [(0, 0x1d, 8)] + ([(0, 0x2a, 8)] if select else []) + [(0, arrow, 9)]
    released = [(vk, scan, flags | 2) for vk, scan, flags in reversed(pressed)]
    assert sent[:len(pressed) + len(released)] == pressed + released
    # SendInput 的失败保护会额外释放修饰键，后续包也必须全部是抬键。
    assert all(flags & 2 for vk, scan, flags in sent[len(pressed) + len(released):])
    assert result['ok'] and result['state'] == 'action_executed'
    assert screenshots == [str(tmp_path / 'screen.png')]
    before = list(sent)
    with pytest.raises(ValueError, match='运行'):
        actions.perform('hotkey', tmp_path, {'keys': 'win+r', 'shell_allowed': False})
    assert sent == before


def test_every_advertised_hotkey_has_native_mapping():
    from lib.windows_desktop_actions import HOTKEYS, hotkey_events
    schema = WINDOWS_DESKTOP_DEFS[0]['function']['parameters']['properties']
    assert set(schema['keys']['enum']) == set(HOTKEYS)
    for keys in HOTKEYS.values():
        assert hotkey_events(keys)


def test_pointer_reference_requires_recent_workspace_image(tmp_path):
    import os
    import pytest
    from PIL import Image
    from lib.windows_desktop import desktop_request
    path = tmp_path / 'uploads/screen.png'
    path.parent.mkdir()
    Image.new('RGB', (2560, 1600)).save(path)
    args = {'action': 'drag', 'reference_image': str(path), 'x': 10, 'y': 20,
            'end_x': 10, 'end_y': 200, 'image_width': 1600, 'image_height': 1000}
    assert desktop_request(args, tmp_path)['screen_size'] == [2560, 1600]
    os.utime(path, (1, 1))
    with pytest.raises(ValueError, match='重新截图'):
        desktop_request(args, tmp_path)


def test_wheel_sends_whole_notches():
    from lib.windows_desktop_actions import wheel_events
    # 每格 120; 旧实现把 10 原样当增量 (不足一格), 微信 4.x 不响应
    assert wheel_events(3) == [120, 120, 120]
    assert wheel_events(-2) == [-120, -120]


def test_wechat_match_excludes_wecom():
    from lib.windows_desktop_worker import is_wechat_title
    assert is_wechat_title('微信') and is_wechat_title('WeChat') and is_wechat_title('Weixin')
    assert not is_wechat_title('企业微信') and not is_wechat_title('WXWork') and not is_wechat_title('WeCom')


def test_copy_flow_is_advertised():
    from lib.windows_desktop import ACTIONS
    from lib.windows_desktop_actions import HOTKEYS
    from lib.tools.defs.windows_desktop_defs import WINDOWS_DESKTOP_DEFS
    props = WINDOWS_DESKTOP_DEFS[0]['function']['parameters']['properties']
    assert 'clipboard' in ACTIONS and 'clipboard' in props['action']['enum']
    assert 'right_click' in ACTIONS and 'right_click' in props['action']['enum']
    assert 'ctrl+c' in HOTKEYS and 'ctrl+c' in props['keys']['enum']


def test_send_safety_rules_are_in_tool_description():
    desc = WINDOWS_DESKTOP_DEFS[0]['function']['description']
    assert '多个可能的对象' in desc and '不要自己挑' in desc   # 实测曾在多个备注相近的联系人里自行挑选
    assert '原有用户草稿' in desc and '不要清空' in desc       # 实测曾全选删除输入框草稿


def test_right_click_requires_reference_image(tmp_path):
    import pytest
    from lib.windows_desktop import desktop_request
    with pytest.raises(ValueError, match='reference_image'):
        desktop_request({'action': 'right_click', 'x': 1, 'y': 1}, tmp_path)


def test_pointer_action_reuses_latest_screenshot_of_same_session_and_host():
    from core.tools.handlers.windows_desktop import with_reference, remember_shot, _LAST_SHOT
    _LAST_SHOT.clear()
    click = {'alias': 'pc', 'action': 'click', 'x': 1, 'y': 2}
    assert 'reference_image' not in with_reference('s1', click)        # 没有截图时不臆造
    remember_shot('s1', 'pc', {'image': '/w/uploads/a.png'})
    remember_shot('s1', 'pc', {'image': '/w/uploads/b.png'})
    assert with_reference('s1', click)['reference_image'] == '/w/uploads/b.png'
    assert 'reference_image' not in with_reference('s2', click)        # 不跨会话
    assert 'reference_image' not in with_reference('s1', {**click, 'alias': 'other'})  # 不跨电脑
    assert with_reference('s1', {**click, 'reference_image': '/w/x.png'})['reference_image'] == '/w/x.png'
    assert 'reference_image' not in with_reference('s1', {'alias': 'pc', 'action': 'ocr'})


def test_hover_is_pointer_action():
    from lib.windows_desktop import ACTIONS
    props = WINDOWS_DESKTOP_DEFS[0]['function']['parameters']['properties']
    assert 'hover' in ACTIONS and 'hover' in props['action']['enum']
    assert 'hotkey enter' in WINDOWS_DESKTOP_DEFS[0]['function']['description']
