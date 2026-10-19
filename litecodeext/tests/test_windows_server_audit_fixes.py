"""[2026-10-19] Windows 服务端审计修复回归: 私钥路径不外露 / 请求体先认证再读 / shell 关闭时的桌面旁路 /
SSH 桌面任务 elevated 需管理员授权且任务目录清理 / 凭据不经剪贴板 / GUI 程序脱离节点作业 / 后台巡检不重复追加。"""
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.windows_nodes import Nodes
from lib import windows_desktop_actions as actions


def _seed(nodes, **extra):
    with nodes.locked() as data:
        data['pc'] = {'id': 'a' * 32, 'alias': 'pc', 'state': 'online', 'last_seen': 0, 'expires': 0,
                      'server_url': 'https://example.test', 'capabilities': {'shell': False},
                      'token_hash': 'x', 'ssh_key_file': str(nodes.root / 'ssh' / ('a' * 32)), **extra}


# ---- 1d: list() 不带私钥路径 / .secrets 路径 ----
def test_list_strips_ssh_key_file_and_secret_paths(tmp_path):
    nodes = Nodes(tmp_path)
    _seed(nodes, note_path=str(tmp_path / '.secrets/windows_nodes/capability.key.bin'))
    item = nodes.list()[0]
    assert 'ssh_key_file' not in item and 'note_path' not in item and 'token_hash' not in item
    assert '.secrets' not in json.dumps(item)
    # 原记录保留 (内部仍可用), 只是不对外
    with nodes.locked() as data:
        assert data['pc']['ssh_key_file'].endswith('a' * 32)


# ---- 6: 设备接口先认证再读体; 小接口 64KB 上限 ----
@pytest.fixture
def device_client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    nodes = Nodes(tmp_path)
    app = FastAPI()
    app.include_router(router.router)
    with patch.object(router, 'manager', return_value=nodes):
        yield TestClient(app), nodes, router


def test_status_and_result_reject_unauthenticated_before_reading_body(device_client):
    client, nodes, router = device_client
    big = b'{"x":"' + b'a' * (1024 * 1024) + b'"}'
    with patch.object(router, 'body', side_effect=AssertionError('认证前不得读请求体')):
        for path in ('/api/windows/device/' + 'b' * 32 + '/status',
                     '/api/windows/device/' + 'b' * 32 + '/result/' + 'c' * 32):
            response = client.post(path, content=big, headers={'Authorization': 'Bearer wrong'})
            assert response.status_code == 403
            assert response.headers.get('X-LiteCode-Auth') == 'revoked'


def test_enroll_and_status_cap_body_at_64kb(device_client):
    client, nodes, router = device_client
    big = json.dumps({'code': 'x' * (router.SMALL_BODY + 10)})
    assert client.post('/api/windows/enroll', content=big).status_code == 413
    with patch.object(Nodes, 'authenticate', return_value={}):
        response = client.post('/api/windows/device/' + 'a' * 32 + '/status', content=big,
                               headers={'Authorization': 'Bearer t'})
    assert response.status_code == 413
    assert router.RESULT_BODY > router.SMALL_BODY


# ---- 2: shell 关闭时的旁路 ----
@pytest.mark.parametrize('name', ['vscode', 'code', 'VSCode'])
def test_vscode_needs_shell_capability(name):
    with pytest.raises(ValueError, match='shell'):
        actions.shell_guard('launch_app', {'name': name}, False)
    actions.shell_guard('launch_app', {'name': name}, True)
    actions.shell_guard('launch_app', {'name': 'notepad'}, False)


@pytest.mark.parametrize('keys', ['ctrl+shift+esc', 'Esc+Shift+Ctrl', 'win+r', 'R+Win'])
def test_task_manager_and_run_hotkeys_need_shell(keys):
    with pytest.raises(ValueError, match='shell'):
        actions.shell_guard('hotkey', {'keys': keys}, False)
    actions.shell_guard('hotkey', {'keys': keys}, True)


@pytest.mark.parametrize('process', ['Code.exe', 'taskmgr.exe', 'WindowsTerminal.exe', 'cmd.exe', 'powershell.exe'])
def test_no_keyboard_input_into_shell_hosts_when_shell_off(process):
    for action, text in (('hotkey', ''), ('type_text', 'hello'), ('type_secret', 'pw')):
        with pytest.raises(ValueError, match='shell'):
            actions.input_guard(action, text, process, False)
        actions.input_guard(action, text, process, True)


@pytest.mark.parametrize('text', ['powershell', 'cmd /c whoami', 'pwsh -c x', 'wt', 'C:\\Windows\\System32\\cmd.exe',
                                  '"powershell.exe" -nop', '%ComSpec%', 'wsl'])
def test_explorer_address_bar_rejects_shell_commands(text):
    for process in ('explorer.exe', 'SearchHost.exe'):
        with pytest.raises(ValueError, match='shell'):
            actions.input_guard('type_text', text, process, False)
    actions.input_guard('type_text', text, 'explorer.exe', True)
    actions.input_guard('type_text', text, 'notepad.exe', False)   # 普通编辑器里打这些字不受限


def test_explorer_ordinary_text_and_hotkeys_still_allowed():
    actions.input_guard('type_text', 'C:\\Users\\test\\Documents', 'explorer.exe', False)
    actions.input_guard('type_text', '季度报表', 'explorer.exe', False)
    actions.input_guard('hotkey', '', 'explorer.exe', False)
    actions.input_guard('type_text', 'powershell', '', False)   # 取不到前台进程时不误拦普通窗口


def test_perform_type_text_checks_foreground_window(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, 'foreground_process', lambda: 'explorer.exe')
    monkeypatch.setitem(sys.modules, 'pyautogui', MagicMock())
    monkeypatch.setitem(sys.modules, 'mss', MagicMock())
    monkeypatch.setattr(actions.ctypes, 'windll', MagicMock(), raising=False)
    with patch.object(actions, 'type_unicode') as typed, pytest.raises(ValueError, match='shell'):
        actions.perform('type_text', tmp_path, {'text': 'powershell', 'input_method': 'unicode'})
    typed.assert_not_called()


# ---- 4: 凭据不经剪贴板 ----
def test_type_secret_request_forces_unicode(tmp_path):
    from lib.windows_desktop import desktop_request
    store = tmp_path / '.secrets/windows_credentials.json'
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({'mail': {'password': 'S3cret!'}}))
    request = desktop_request({'action': 'type_secret', 'credential': 'mail', 'field': 'password',
                               'input_method': 'clipboard'}, tmp_path)
    assert request['input_method'] == 'unicode' and request['sensitive'] is True


def test_sensitive_text_never_touches_clipboard(tmp_path, monkeypatch):
    clip = MagicMock()
    monkeypatch.setitem(sys.modules, 'pyperclip', clip)
    monkeypatch.setitem(sys.modules, 'pyautogui', MagicMock())
    monkeypatch.setitem(sys.modules, 'mss', MagicMock())
    monkeypatch.setattr(actions.ctypes, 'windll', MagicMock(), raising=False)
    monkeypatch.setattr(actions, 'foreground_process', lambda: 'chrome.exe')
    monkeypatch.setattr(actions, 'list_windows', lambda: [])
    monkeypatch.setattr(actions.time, 'sleep', lambda _: None)
    with patch.object(actions, 'type_unicode') as typed, patch.object(actions, 'send_keyboard') as keys:
        actions.perform('type_text', tmp_path, {'text': 'S3cret!', 'input_method': 'clipboard', 'sensitive': True})
    typed.assert_called_once_with('S3cret!')
    keys.assert_not_called()
    clip.copy.assert_not_called()


# ---- 7: GUI 程序脱离节点计划任务的作业对象 ----
def test_detached_popen_breaks_away_from_job():
    popen = MagicMock()
    actions.detached_popen(['notepad.exe'], cwd='C:/x', popen=popen)
    flags = popen.call_args.kwargs['creationflags']
    assert flags & actions.CREATE_BREAKAWAY_FROM_JOB and flags & actions.CREATE_NEW_PROCESS_GROUP
    assert popen.call_args.kwargs['shell'] is False


def test_detached_popen_falls_back_when_breakaway_denied():
    denied = OSError(13, 'Access is denied')
    denied.winerror = 5
    popen = MagicMock(side_effect=[denied, 'process'])
    assert actions.detached_popen(['notepad.exe'], popen=popen) == 'process'
    assert not popen.call_args.kwargs['creationflags'] & actions.CREATE_BREAKAWAY_FROM_JOB
    other = MagicMock(side_effect=FileNotFoundError(2, 'missing'))
    with pytest.raises(FileNotFoundError):
        actions.detached_popen(['missing.exe'], popen=other)
    assert other.call_count == 1


def test_launch_app_uses_detached_popen(monkeypatch):
    monkeypatch.setattr(actions, 'documents_dir', lambda: Path('/tmp'))
    with patch('subprocess.Popen') as launch:
        actions.launch_app({'name': 'notepad'})
    assert launch.call_args.kwargs['creationflags'] & actions.CREATE_BREAKAWAY_FROM_JOB


# ---- 3 + 5: SSH 通道 elevated 需管理员授权; 任务目录整体删除 ----
def _run_ssh_desktop(tmp_path, monkeypatch, env=None):
    from lib import windows_desktop
    scripts = []

    def fake_powershell(client, script, timeout=30):
        scripts.append(script)
        if 'ConvertTo-Json' in script:
            return json.dumps({'home': 'C:\\Users\\test', 'python': 'C:\\Python\\python.exe', 'user': 'PC\\test'})
        return ''
    client = MagicMock()
    sftp = client.open_sftp.return_value
    sftp.open.return_value.__enter__.return_value.read.return_value = b'{"ok": true, "state": "listed"}'
    if env is None:
        monkeypatch.delenv(windows_desktop.ELEVATED_ENV, raising=False)
    else:
        monkeypatch.setenv(windows_desktop.ELEVATED_ENV, env)
    conn = {'host': 'h', 'port': 22, 'user': 'u', 'password': 'p', 'key_file': '', 'jump': None}
    with patch('core.tools.handlers.remote._conn', return_value=(conn, '')), \
         patch('core.tools.handlers.remote._connect', return_value=(client, None)), \
         patch('core.tools.handlers.remote._close'), \
         patch.object(windows_desktop, 'powershell', side_effect=fake_powershell):
        result = windows_desktop.execute({'alias': 'win-ssh', 'action': 'windows', 'elevated': True, 'timeout': 10},
                                         tmp_path)
    return result, scripts


def test_ssh_elevated_ignored_without_admin_authorization(tmp_path, monkeypatch):
    result, scripts = _run_ssh_desktop(tmp_path, monkeypatch)
    register = next(s for s in scripts if 'Register-ScheduledTask' in s)
    assert '-RunLevel Limited' in register and 'Highest' not in register
    assert 'elevated' in result['elevated_note']


def test_ssh_elevated_honored_for_admin_listed_alias(tmp_path, monkeypatch):
    result, scripts = _run_ssh_desktop(tmp_path, monkeypatch, env='other, win-ssh')
    assert '-RunLevel Highest' in next(s for s in scripts if 'Register-ScheduledTask' in s)
    assert 'elevated_note' not in result


def test_ssh_desktop_task_folder_removed_after_task(tmp_path, monkeypatch):
    _, scripts = _run_ssh_desktop(tmp_path, monkeypatch)
    cleanup = [s for s in scripts if 'Remove-Item' in s]
    assert len(cleanup) == 1 and '/AppData/Local/LiteCodeDesktop/LiteCode-Desktop-' in cleanup[0]
    assert scripts.index(cleanup[0]) > scripts.index(next(s for s in scripts if 'Unregister-ScheduledTask' in s))


# ---- 8: 后台巡检不重复追加刚结束的本页轮次 ----
def test_chat_attach_registers_settling_synchronously_and_uses_tail():
    source = (ROOT / 'web_assets/chat_attach.js').read_text()
    after = source[source.index('async function afterTurn'):]
    first_await = after.index('await ')
    assert after.index('settling.add(sid)') < first_await and after.index('turnSeq++') < first_await
    poll = source[source.index('async function pollBackground'):]
    assert 'settling.has(sid)' in poll and 'seq !== turnSeq' in poll
    assert '/api/sessions/${sid}${pageTail()}' in poll
