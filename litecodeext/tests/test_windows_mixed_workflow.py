"""SSH/后台与 OCR 混用的边界；原生桌面和 SSH 传输由替身提供，真机另行验收。"""
import asyncio
import ctypes
import json
from pathlib import Path
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib import windows_desktop_ocr as ocr


def frame(text='保存', x=100, y=100, origin=(0, 0)):
    return {'lines': [{'text': text, 'rect': {'x': x, 'y': y, 'width': 80, 'height': 20}}],
            'width': 3840, 'height': 2160, 'origin': list(origin),
            'language': 'zh-Hans', 'ocr_scale': 1}


@pytest.fixture
def native_desktop(monkeypatch):
    mouse = types.SimpleNamespace(click=Mock(), FAILSAFE=True)
    monkeypatch.setitem(sys.modules, 'pyautogui', mouse)
    monkeypatch.setattr(ctypes, 'windll', types.SimpleNamespace(
        user32=types.SimpleNamespace(SetProcessDPIAware=Mock())), raising=False)
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    return mouse


def test_click_text_refreshes_ocr_after_background_changes_window(tmp_path, native_desktop):
    old = frame(x=10, y=20)
    current = frame(x=500, y=300, origin=(-3840, 0))
    after = frame('已保存')
    with patch.object(ocr, 'capture_ocr', side_effect=[old, current, after]) as capture:
        observed = ocr.perform('ocr', tmp_path, {})
        clicked = ocr.perform('click_text', tmp_path, {'text': '保存'})
    assert observed['lines'] == old['lines']
    native_desktop.click.assert_called_once_with(-3300, 310)
    assert clicked['target'] == current['lines'][0]
    assert clicked['after_lines'] == after['lines']
    assert capture.call_count == 3


@pytest.mark.parametrize('matches,state', [(0, 'target_not_found'), (2, 'target_not_unique')])
def test_background_change_removing_or_duplicating_target_never_clicks(tmp_path, native_desktop, matches, state):
    current = frame()
    current['lines'] *= matches
    with patch.object(ocr, 'capture_ocr', return_value=current) as capture:
        result = ocr.perform('click_text', tmp_path, {'text': '保存'})
    # [2026-10-19] 0 个命中不再误报 target_not_unique
    assert result['ok'] is False and result['state'] == state
    native_desktop.click.assert_not_called()
    capture.assert_called_once()


@pytest.mark.parametrize('target', ['', '保', '另存为'])
def test_ocr_does_not_guess_partial_or_missing_target(tmp_path, native_desktop, target):
    with patch.object(ocr, 'capture_ocr', return_value=frame()):
        result = ocr.perform('click_text', tmp_path, {'text': target})
    assert result['ok'] is False
    native_desktop.click.assert_not_called()


def test_compressed_image_keeps_full_resolution_ocr_click_coordinates(tmp_path, native_desktop):
    from lib import windows_desktop_actions as actions
    current = frame(x=3000, y=1800, origin=(-3840, -100))

    def capture(folder, name='screen.png'):
        Image.new('RGB', (3840, 2160), 'white').save(folder / name)
        return current

    with patch.object(ocr, 'capture_ocr', side_effect=capture), \
         patch.dict(sys.modules, {'windows_desktop_actions': actions}):
        result = ocr.perform('click_text', tmp_path, {'text': '保存', 'compress': True})
    native_desktop.click.assert_called_once_with(-800, 1710)
    assert result['screen_size'] == [3840, 2160]
    assert max(result['image_size']) <= 1600
    with Image.open(tmp_path / result['image']) as image:
        assert image.size == tuple(result['image_size']) and image.format == 'JPEG'


def test_lock_unlock_does_not_block_background_files_or_poison_next_ocr(tmp_path):
    from lib import windows_desktop_worker as worker, windows_file_actions as files
    perform = Mock(return_value={'ok': True, 'state': 'ocr_ready', 'lines': frame()['lines']})
    (tmp_path / 'ssh-created.txt').write_text('SSH READY', encoding='utf-8')
    with patch.dict(sys.modules, {'windows_file_actions': files,
                                 'windows_desktop_ocr': types.SimpleNamespace(perform=perform)}), \
         patch.object(worker, 'desktop_ready') as ready:
        for unlocked in [False, True, False, True]:
            ready.return_value = unlocked
            listing = worker.run('list_dir', tmp_path, {'path': str(tmp_path)})
            assert listing['ok'] and any(e['name'] == 'ssh-created.txt' for e in listing['entries'])
            result = worker.run('ocr', tmp_path, {})
            assert result['ok'] is unlocked
            assert result['state'] == ('ocr_ready' if unlocked else 'desktop_unavailable')
    assert perform.call_count == 2


def test_failed_ssh_does_not_poison_ocr_session_or_its_screenshot(tmp_path):
    from core.tools.handlers import remote, windows_desktop as handler
    shot = str(tmp_path / 'uploads/ocr.png')
    result = {'ok': True, 'state': 'ocr_ready', 'lines': frame()['lines'], 'image': shot}
    connection = {'host': '127.0.0.1', 'port': 2222, 'user': 'test',
                  'password': '', 'key_file': '/test/key', 'jump': None}

    async def run():
        ssh, _ = await remote.h_ssh_exec('mixed-session', {'alias': 'ssh-pc', 'cmd': 'hostname'})
        desktop, _ = await handler.h_windows_desktop('mixed-session', {'alias': 'node-pc', 'action': 'ocr'})
        return ssh, desktop

    handler._LAST_SHOT.pop(('mixed-session', 'node-pc'), None)
    try:
        with patch.object(remote, '_conn', return_value=(connection, '')), \
             patch.object(remote, '_ssh', new=AsyncMock(side_effect=TimeoutError('SSH disconnected'))), \
             patch('lib.config.WORKSPACE', tmp_path), \
             patch('lib.windows_desktop.execute', return_value=result), \
             patch('lib.agent.subagent_operator.check_windows_call', side_effect=lambda sid, name, args: (args, None)), \
             patch('core.tool_dispatch._add_artifact'):
            ssh, desktop = asyncio.run(run())
        assert ssh.startswith('ERROR: ssh_exec')
        assert json.loads(desktop.split('\n')[0])['state'] == 'ocr_ready'
        assert handler.with_reference('mixed-session', {'alias': 'node-pc', 'action': 'click'})['reference_image'] == shot
        assert 'reference_image' not in handler.with_reference('other-session', {'alias': 'node-pc', 'action': 'click'})
    finally:
        handler._LAST_SHOT.pop(('mixed-session', 'node-pc'), None)


@pytest.mark.parametrize('variable,relative', [
    ('LOCALAPPDATA', 'Programs/Microsoft VS Code/Code.exe'),
    ('ProgramFiles', 'Microsoft VS Code/Code.exe'),
    ('ProgramFiles(x86)', 'Microsoft VS Code/Code.exe'),
])
def test_vscode_launch_finds_user_and_system_installs_with_shell(tmp_path, monkeypatch, variable, relative):
    # [2026-10-19] VS Code 自带终端/命令面板, shell 关闭时不再可启动 (见 test_windows_server_audit_fixes)
    from lib import windows_desktop_actions as actions
    for name in ['LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)']:
        monkeypatch.delenv(name, raising=False)
    base = tmp_path / 'Directory With Spaces'
    executable = base / relative
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv(variable, str(base))
    with patch('subprocess.Popen') as launch:
        result = actions.launch_app({'name': 'vscode', 'shell_allowed': True})
    assert result == {'method': 'mapping', 'launched': 'vscode', 'as_admin': False}
    assert launch.call_args.args[0] == [str(executable)]
    assert launch.call_args.kwargs['shell'] is False


def test_missing_vscode_does_not_launch_an_unrelated_start_menu_result(monkeypatch):
    from lib import windows_desktop_actions as actions
    for name in ['LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)']:
        monkeypatch.delenv(name, raising=False)
    with patch('subprocess.Popen') as launch, patch.object(actions, 'send_keyboard') as keyboard:
        with pytest.raises(ValueError, match='VS Code'):
            actions.launch_app({'name': 'code', 'shell_allowed': True})
    launch.assert_not_called()
    keyboard.assert_not_called()


@pytest.mark.parametrize('requested,expected', [('auto', 'zh-Hans-CN'), ('en-US', 'en-US'), ('en', 'en-US'), ('fr-FR', None)])
def test_ocr_selects_requested_installed_language(tmp_path, monkeypatch, requested, expected):
    screen = MagicMock()
    screen.__enter__.return_value = screen
    screen.monitors = [{}, {'left': 0, 'top': 0}]
    screen.grab.return_value = types.SimpleNamespace(size=(20, 10), rgb=b'\xff' * 600)
    recognize = Mock(return_value={'lines': []})
    engine = types.SimpleNamespace(available_recognizer_languages=[
        types.SimpleNamespace(language_tag=tag) for tag in ['zh-Hans-CN', 'en-US']], max_image_dimension=4096)
    monkeypatch.setitem(sys.modules, 'mss', types.SimpleNamespace(MSS=lambda: screen))
    monkeypatch.setitem(sys.modules, 'winocr', types.SimpleNamespace(recognize_pil_sync=recognize))
    monkeypatch.setitem(sys.modules, 'winrt.windows.media.ocr', types.SimpleNamespace(OcrEngine=engine))
    if expected is None:
        with pytest.raises(ValueError, match='可用语言'):
            ocr.capture_ocr(tmp_path, language=requested)
        recognize.assert_not_called()
        return
    result = ocr.capture_ocr(tmp_path, language=requested)
    assert result['language'] == expected
    assert recognize.call_args.kwargs['lang'] == expected
    assert result['available_languages'] == ['zh-Hans-CN', 'en-US']


def test_explicit_ocr_language_survives_desktop_request(tmp_path):
    from lib.windows_desktop import desktop_request
    assert desktop_request({'action': 'ocr', 'ocr_language': 'en-US'}, tmp_path)['ocr_language'] == 'en-US'


@pytest.mark.parametrize('observed,passed', [
    ('LCMIX379687 SSH READY 24680', True),
    ('LCMIX379687  SSH READY\t24680', True),
    ('LCMIX379687 SSH READY 24580', False),
    ('LCMlX379687 SSH READY 24680', False),
    ('LCMIX379687 SSH READY 246800', False),
])
def test_ocr_verdict_checks_actual_characters_and_digits(tmp_path, native_desktop, observed, passed):
    with patch.object(ocr, 'capture_ocr', return_value=frame(observed)):
        result = ocr.perform('ocr', tmp_path, {'expected_texts': ['LCMIX379687 SSH READY 24680']})
    assert result['ok'] is passed
    assert result['state'] == ('ocr_verified' if passed else 'ocr_mismatch')
    assert result['assertions'][0]['matched'] is passed
    native_desktop.click.assert_not_called()


def test_ocr_does_not_assemble_an_expected_line_from_unrelated_rows():
    lines = [{'text': '246'}, {'text': '80'}]
    assert ocr.verify_lines(lines, ['24680'])[0]['matched'] is False


@pytest.mark.parametrize('expected', [[], [''], [' '], '24680', [None], ['x' * 201], ['x'] * 11])
def test_invalid_ocr_assertions_are_refused_before_sending_job(tmp_path, expected):
    from lib.windows_desktop import desktop_request
    with pytest.raises(ValueError, match='expected_texts'):
        desktop_request({'action': 'ocr', 'expected_texts': expected}, tmp_path)
