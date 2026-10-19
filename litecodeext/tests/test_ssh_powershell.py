"""Windows SSH 脚本跨默认 shell 安全传递；PowerShell 中执行真实引号/中文/换行。"""
import asyncio
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from core.tools.handlers import remote
from lib.windows_ssh import POWERSHELL_COMMAND_LIMIT, powershell_command, powershell_file_command, powershell_script

CONN = {'host': 'example.test', 'port': 22, 'user': 'test', 'password': '',
        'key_file': '/test/key', 'jump': None}


def invoke(args):
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock(return_value=(0, 'ok', ''))) as ssh:
        text, _ = asyncio.run(remote.h_ssh_exec('powershell-test', args))
    return text, ssh


def test_powershell_preserves_variables_quotes_chinese_and_newlines(tmp_path):
    content = '中文 "$HOME"\n`n $() 原样'
    script = "$value = @'\n" + content + "\n'@\n[IO.File]::WriteAllText('" + str(tmp_path / 'roundtrip.txt') + "', $value)"
    text, ssh = invoke({'alias': 'windows', 'shell': 'powershell', 'cmd': script})
    command = ssh.call_args.args[4]
    assert 'rc=0' in text and command.startswith('powershell.exe -NoProfile -NonInteractive -EncodedCommand ')
    decoded = base64.b64decode(command.split()[-1]).decode('utf-16le')
    assert decoded.endswith(script)
    assert ssh.call_args.kwargs == {}  # 短脚本不引入 SFTP 或其它新投递参数。
    assert '$value' not in command and content not in command
    executable = shutil.which('pwsh')
    if not executable:
        pytest.skip('未安装 PowerShell，编码检查已执行')
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', command.split()[-1]],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'roundtrip.txt').read_text() == content


@pytest.mark.parametrize('shell', [None, 'auto'])
def test_default_ssh_commands_are_preserved(shell):
    args = {'alias': 'linux', 'cmd': 'printf ok && id'}
    if shell is not None:
        args['shell'] = shell
    _, ssh = invoke(args)
    assert ssh.call_args.args[4] == args['cmd']


def test_unknown_shell_is_rejected_without_remote_execution():
    text, ssh = invoke({'cmd': 'hostname', 'shell': 'bash-typo'})
    assert text.startswith('ERROR: ssh_exec')
    ssh.assert_not_called()


def test_powershell_failure_returns_remote_error_code():
    # [2026-10-19] 原测试 mock 返回 1 再断言 rc=1, 恒真。改为检查发给 _ssh 的命令文本:
    # 脚本原样位于引导之后、没有被 try/catch 或 ScriptBlock 包住吞掉退出码, 且非 0/1 的退出码原样透传。
    script = "Get-Service missing\nexit 17"
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock(return_value=(17, '', 'missing service'))) as ssh:
        text, _ = asyncio.run(remote.h_ssh_exec('powershell-test', {'cmd': script, 'shell': 'powershell'}))
    decoded = base64.b64decode(ssh.call_args.args[4].split()[-1]).decode('utf-16le')
    prelude, _, body = decoded.partition('\n')
    assert body == script
    assert "$ErrorActionPreference='Stop'" in prelude
    for swallow in ('try', 'catch', 'ScriptBlock', 'exit 0', '$LASTEXITCODE'):
        assert swallow not in prelude
    assert 'rc=17' in text and 'missing service' in text
    # 文件载体: 末条命令失败才补 exit 1, 显式 exit N 不受影响。
    file_boot = base64.b64decode(powershell_file_command('C:/t/x.ps1').split()[-1]).decode('utf-16le')
    assert file_boot.rstrip().endswith('"`nif (-not `$?) { exit 1 }"))')


@pytest.mark.parametrize('partial', [b'', b'installation still running\n'])
def test_ssh_deadline_never_reports_success(partial):
    channel = MagicMock()
    channel.exit_status_ready.return_value = False
    channel.recv_ready.side_effect = [bool(partial), False, False]
    channel.recv_stderr_ready.return_value = False
    channel.recv.return_value = partial
    client = MagicMock()
    client.exec_command.return_value = (None, SimpleNamespace(channel=channel), None)
    fake_paramiko = SimpleNamespace(SSHClient=lambda: client, AutoAddPolicy=lambda: None)
    # 前两次 monotonic 是 _run_ssh 连接前/后计时 ([2026-10-19] 命令预算从连接前起算)
    with patch.dict(sys.modules, {'paramiko': fake_paramiko}), \
         patch.object(remote.time, 'monotonic', side_effect=[0, 0, 0, 0, 2]), \
         patch.object(remote.time, 'sleep'):
        rc, out, err = remote._run_ssh('example.test', 22, 'test', '', 'install', 1)
    assert rc == 124 and out == partial.decode()
    assert 'timed out' in err and 'completion is unknown' in err
    channel.recv_exit_status.assert_not_called()
    channel.close.assert_called_once()
    client.close.assert_called_once()


def test_long_powershell_handler_uses_bom_file_payload_instead_of_command_line():
    script = "# " + '长安装脚本' * 3000 + "\nWrite-Output 'worker-started'\n"
    assert len(powershell_command(script)) > 8191
    text, ssh = invoke({'alias': 'windows', 'shell': 'powershell', 'cmd': script})
    assert 'rc=0' in text and ssh.call_args.args[4] == ''
    payload = ssh.call_args.kwargs['powershell_script']
    assert payload.startswith(b'\xef\xbb\xbf')
    assert payload.decode('utf-8-sig') == powershell_script(script)
    assert script not in text


@pytest.mark.parametrize('exit_code', [0, 17])
def test_file_bootstrap_executes_real_unicode_script_and_keeps_exit_code(tmp_path, exit_code):
    executable = os.environ.get('LITECODE_TEST_PWSH') or shutil.which('pwsh')
    if not executable:
        pytest.skip('需要本地 PowerShell 验证文件载体')
    path = tmp_path / "中文 O'Brien O’Brien %temp% script.ps1"
    content = '中文🙂 "$HOME" `n $() 原样'
    script = ("# " + 'x' * 12000 + "\n$value = @'\n" + content + "\n'@\n"
              "[Console]::Out.WriteLine($value)\n[Console]::Error.WriteLine('warning 中文')\n"
              f'exit {exit_code}\n')
    path.write_bytes(powershell_script(script).encode('utf-8-sig'))
    command = powershell_file_command(str(path))
    assert len(command) < POWERSHELL_COMMAND_LIMIT and content not in command
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', command.split()[-1]],
                            capture_output=True, timeout=15)
    assert result.returncode == exit_code
    assert result.stdout.decode('utf-8').strip() == content
    assert result.stderr.decode('utf-8').strip() == 'warning 中文'


def test_file_bootstrap_does_not_hide_final_command_failure(tmp_path):
    executable = os.environ.get('LITECODE_TEST_PWSH') or shutil.which('pwsh')
    if not executable:
        pytest.skip('需要本地 PowerShell 验证文件载体')
    script = "& /bin/sh -c 'exit 9'"
    path = tmp_path / 'native-failure.ps1'
    path.write_bytes(powershell_script(script).encode('utf-8-sig'))
    codes = []
    for command in (powershell_command(script), powershell_file_command(str(path))):
        result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', command.split()[-1]],
                                capture_output=True, timeout=15)
        codes.append(result.returncode)
    assert codes == [1, 1]  # 沿用 EncodedCommand 的语义，不能因外层 ScriptBlock 掩盖失败。


@pytest.fixture
def file_transport():
    client = MagicMock()
    sftp = client.open_sftp.return_value
    handle = sftp.open.return_value
    temp = "C:\\Users\\中文 O'Brien O’Brien %name%\\AppData\\Local\\Temp\\"
    read = MagicMock(side_effect=[(0, temp, ''), (17, 'partial 中文', 'worker failed')])
    return SimpleNamespace(client=client, sftp=sftp, handle=handle, temp=temp, read=read)


def test_file_delivery_uses_same_connection_windows_path_and_exact_cleanup(file_transport):
    f = file_transport
    payload = powershell_script("Write-Output '中文'; exit 17").encode('utf-8-sig')
    fake_paramiko = SimpleNamespace(SSHClient=lambda: f.client, AutoAddPolicy=lambda: None)
    jump_client, sock = MagicMock(), object()
    with patch.dict(sys.modules, {'paramiko': fake_paramiko}), \
         patch.object(remote, '_open_jump', return_value=(jump_client, sock)), \
         patch.object(remote, '_read_ssh_command', f.read):
        result = remote._run_ssh('example.test', 22, 'test', '', '', 30, '/test/key',
                                 {'host': 'jump.test'}, powershell_script=payload)
    assert result == (17, 'partial 中文', 'worker failed')
    assert f.client.connect.call_args.kwargs['sock'] is sock
    assert f.client.connect.call_args.kwargs['key_filename'] == '/test/key'
    path, mode = f.sftp.open.call_args.args
    assert path.startswith(f.temp.replace('\\', '/')) and path.endswith('.ps1') and mode == 'wx'
    f.handle.write.assert_called_once_with(payload)
    f.handle.flush.assert_called_once()
    f.sftp.remove.assert_called_once_with(path)
    f.sftp.mkdir.assert_not_called()
    f.sftp.close.assert_called_once()
    f.client.close.assert_called_once()
    jump_client.close.assert_called_once()
    for call in f.read.call_args_list:
        assert call.args[0] is f.client and len(call.args[1]) < POWERSHELL_COMMAND_LIMIT
        assert payload.decode('utf-8-sig') not in call.args[1]


def test_file_execution_timeout_preserves_partial_output_and_cleans_only_own_file(file_transport):
    f = file_transport
    f.read.side_effect = [(0, f.temp, ''), (124, 'worker running', 'remote completion is unknown')]
    with patch.object(remote, '_read_ssh_command', f.read):
        result = remote._run_powershell_file(f.client, b'\xef\xbb\xbf# payload', 30)
    assert result == (124, 'worker running', 'remote completion is unknown')
    f.sftp.remove.assert_called_once_with(f.sftp.open.call_args.args[0])


def test_file_upload_timeout_never_executes_and_still_cleans(file_transport):
    f = file_transport
    f.handle.write.side_effect = TimeoutError('upload stalled')
    with patch.object(remote, '_read_ssh_command', f.read):
        rc, out, error = remote._run_powershell_file(f.client, b'\xef\xbb\xbf# payload', 30)
    assert rc == 124 and out == '' and 'completion is unknown' in error
    assert f.read.call_count == 1
    f.sftp.remove.assert_called_once_with(f.sftp.open.call_args.args[0])


def test_async_file_deadline_does_not_claim_cleanup_completed():
    with patch.object(remote.asyncio, 'get_event_loop', return_value=MagicMock()), \
         patch.object(remote.asyncio, 'wait_for', new=AsyncMock(side_effect=asyncio.TimeoutError)), \
         pytest.raises(TimeoutError, match='completion is unknown.*cleanup may still be pending'):
        asyncio.run(remote._ssh('example.test', 22, 'test', '', '', 1,
                                powershell_script=b'\xef\xbb\xbf# payload'))


def test_file_cleanup_failure_is_reported_without_masking_result(file_transport):
    f = file_transport
    f.sftp.remove.side_effect = OSError('busy')
    with patch.object(remote, '_read_ssh_command', f.read):
        rc, out, error = remote._run_powershell_file(f.client, b'\xef\xbb\xbf# payload', 30)
    assert rc == 17 and out == 'partial 中文' and error.startswith('worker failed')
    assert 'cleanup was not confirmed' in error
    f.sftp.close.assert_called_once()


def test_exclusive_create_failure_does_not_delete_someone_elses_file(file_transport):
    f = file_transport
    f.sftp.open.side_effect = FileExistsError('already exists')
    with patch.object(remote, '_read_ssh_command', f.read), pytest.raises(FileExistsError):
        remote._run_powershell_file(f.client, b'\xef\xbb\xbf# payload', 30)
    assert f.read.call_count == 1
    f.sftp.remove.assert_not_called()
    f.sftp.close.assert_called_once()


def test_completed_ssh_keeps_actual_exit_code():
    channel = MagicMock()
    channel.exit_status_ready.return_value = True
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.recv_exit_status.return_value = 7
    client = MagicMock()
    client.exec_command.return_value = (None, SimpleNamespace(channel=channel), None)
    fake_paramiko = SimpleNamespace(SSHClient=lambda: client, AutoAddPolicy=lambda: None)
    with patch.dict(sys.modules, {'paramiko': fake_paramiko}):
        rc, out, err = remote._run_ssh('example.test', 22, 'test', '', 'exit 7', 1)
    assert (rc, out, err) == (7, '', '')
    channel.close.assert_not_called()
    client.close.assert_called_once()
