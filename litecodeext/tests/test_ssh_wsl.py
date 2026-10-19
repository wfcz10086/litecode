"""执行实际 PowerShell→子进程→Bash，验证 WSL 载体的文本与退出码边界。"""
import asyncio
import base64
import json
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
from lib.windows_ssh import wsl_command

CONN = {'host': 'example.test', 'port': 22, 'user': 'test', 'password': '',
        'key_file': '/test/key', 'jump': None}


@pytest.fixture
def carrier(tmp_path):
    executable = os.environ.get('LITECODE_TEST_PWSH') or shutil.which('pwsh')
    if not executable:
        pytest.skip('需要 PowerShell 执行跨进程载体测试')
    windir = tmp_path / 'Windows with spaces'
    system32 = windir / 'System32'
    system32.mkdir(parents=True)
    shim = system32 / 'wsl.exe'
    shim.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['LITECODE_WSL_ARGV_RECORD']).write_text(json.dumps(args))
if args[args.index('--distribution')+1]=='missing':
    sys.stderr.buffer.write('WSL distribution 中文 does not exist\\n'.encode('utf-16le'))
    sys.exit(23)
start=args.index('--exec')+1
os.execvp(args[start],args[start:])
''')
    shim.chmod(0o700)
    temp = tmp_path / 'process-temp'
    temp.mkdir()
    record = tmp_path / 'argv.json'

    def run(script, distro='Ubuntu', user='root', timeout=None):
        command, stdin = wsl_command(script, distro, user, timeout=timeout)
        env = dict(os.environ, WINDIR=str(windir), TMPDIR=str(temp), LITECODE_WSL_ARGV_RECORD=str(record))
        result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', command.split()[-1]],
                                input=stdin, capture_output=True, env=env, timeout=25)
        assert not list(temp.glob('LiteCodeWsl-*')), '执行结束应清理输入和分流日志'
        return result, json.loads(record.read_text())

    return run


@pytest.mark.parametrize('exit_code', [0, 17])
def test_real_bash_unicode_warnings_and_exit_code(carrier, exit_code):
    value = '中文🙂 "$HOME" `n && | %'
    script = f"\ufeffprintf '%s\\n' '{value}'\r\nprintf '%s\\n' 'warning 中文' >&2\r\nexit {exit_code}\r\n"
    result, _ = carrier(script)
    assert result.returncode == exit_code, result.stderr.decode(errors='replace')
    assert result.stdout.decode('utf-8') == value + '\n'
    assert result.stderr.decode('utf-8') == 'warning 中文\n'


def test_long_script_does_not_use_windows_command_line(carrier):
    script = '#' + 'x' * 100_000 + "\nprintf 'long-script-ok\\n'\n"
    command, payload = wsl_command(script, 'Ubuntu')
    assert len(command) < 1500 and len(payload) > 100_000
    result, _ = carrier(script)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert result.stdout == b'long-script-ok\n'


def test_distribution_and_user_are_literal_arguments(carrier):
    distro = "test 'quoted' \"words\" & untouched"
    user = 'user name'
    result, argv = carrier("printf 'literal-args-ok\\n'", distro, user)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert argv[argv.index('--distribution') + 1] == distro
    assert argv[argv.index('--user') + 1] == user
    assert result.stdout == b'literal-args-ok\n'


def test_curly_quote_names_stay_literal_arguments(carrier):
    # [2026-10-19] ’ (U+2019) 在 PowerShell 里也是单引号; 原先只翻倍 ASCII ' 会截断字符串甚至注入。
    distro, user = "O’Brien’; Write-Output pwned; ’", 'O’Brien'
    result, argv = carrier("printf 'curly-ok\\n'", distro, user)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert argv[argv.index('--distribution') + 1] == distro
    assert argv[argv.index('--user') + 1] == user
    assert result.stdout == b'curly-ok\n'


def test_invalid_utf8_output_keeps_real_output_and_exit_code(carrier):
    # [2026-10-19] GBK/二进制输出原先让严格 UTF-8 解码抛异常, 真实输出与退出码丢失变 rc=1。
    # stderr 不用 \xff\xfe 开头: 那是 UTF-16 BOM, wsl.exe 自己的报错就是 UTF-16, 按 BOM 解码是对的
    result, _ = carrier("printf 'ok-\\xd6\\xd0\\xce\\xc4\\n'; printf '\\xd6\\xd0-err\\n' >&2; exit 5")
    assert result.returncode == 5, result.stderr.decode(errors='replace')
    assert result.stdout.decode('utf-8').startswith('ok-')
    assert '-err' in result.stderr.decode('utf-8')


def test_hung_command_times_out_with_124_and_cleans_up(carrier):
    # [2026-10-19] 原先无限 WaitForExit: 卡住的命令一直跑, finally 清理永不执行。预算 = timeout-10 (最少 5s)。
    result, _ = carrier("printf 'before-hang\\n'; sleep 60", timeout=12)
    assert result.returncode == 124
    assert result.stdout == b'before-hang\n'
    assert 'timed out' in result.stderr.decode('utf-8')


def test_wsl_management_utf16_error_is_not_success(carrier):
    result, _ = carrier('echo should-not-run', 'missing')
    assert result.returncode == 23
    assert result.stderr.decode('utf-8') == 'WSL distribution 中文 does not exist\n'
    assert not result.stdout


def test_early_process_failure_with_large_input_keeps_native_error(carrier):
    result, _ = carrier('#' + 'x' * 100_000 + '\necho should-not-run', 'missing')
    assert result.returncode == 23
    assert result.stderr.decode('utf-8') == 'WSL distribution 中文 does not exist\n'


def test_bash_commands_do_not_consume_the_rest_of_script(carrier):
    result, _ = carrier("if read -r unexpected; then exit 9; fi\nprintf 'stdin-eof-ok\\n'\n")
    assert result.returncode == 0
    assert result.stdout == b'stdin-eof-ok\n'


@pytest.mark.parametrize('args', [
    {}, {'wsl_distro': ''}, {'wsl_distro': '-help'},
    {'wsl_distro': 'Ubuntu\nother'}, {'wsl_distro': 'Ubuntu', 'wsl_user': 'root\x00oops'},
    {'wsl_distro': 'Ubuntu', 'cmd': 'echo\x00oops'},
])
def test_invalid_wsl_request_never_connects(args):
    request = {'shell': 'wsl', 'cmd': 'echo ok', **args}
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock()) as ssh:
        text, _ = asyncio.run(remote.h_ssh_exec('wsl-validation', request))
    assert text.startswith('ERROR: ssh_exec')
    ssh.assert_not_called()


@pytest.mark.parametrize('exit_code', [0, 17])
def test_handler_keeps_stderr_and_actual_exit_code(exit_code):
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock(return_value=(exit_code, 'stdout 中文', 'warning'))) as ssh:
        text, _ = asyncio.run(remote.h_ssh_exec('wsl-result', {
            'cmd': 'printf ok', 'shell': 'wsl', 'wsl_distro': 'Ubuntu',
        }))
    assert f'rc={exit_code}' in text and 'stdout 中文' in text and 'warning' in text
    assert ssh.call_args.kwargs['stdin_data']
    base64.b64decode(ssh.call_args.kwargs['stdin_data']).decode('utf-8')


def test_ssh_closes_stdin_before_reading_result():
    channel = MagicMock()
    channel.exit_status_ready.return_value = True
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.recv_exit_status.return_value = 7
    stdin = MagicMock()
    client = MagicMock()
    client.exec_command.return_value = (stdin, SimpleNamespace(channel=channel), None)
    fake = SimpleNamespace(SSHClient=lambda: client, AutoAddPolicy=lambda: None)
    with patch.dict(sys.modules, {'paramiko': fake}):
        rc, _, _ = remote._run_ssh('example.test', 22, 'test', '', 'short bootstrap', 10, stdin_data=b'base64')
    assert rc == 7
    stdin.write.assert_called_once_with(b'base64')
    stdin.flush.assert_called_once()
    stdin.close.assert_called_once()


def test_input_timeout_never_looks_like_success():
    channel = MagicMock()
    stdin = MagicMock()
    stdin.write.side_effect = TimeoutError('input blocked')
    client = MagicMock()
    client.exec_command.return_value = (stdin, SimpleNamespace(channel=channel), None)
    fake = SimpleNamespace(SSHClient=lambda: client, AutoAddPolicy=lambda: None)
    with patch.dict(sys.modules, {'paramiko': fake}):
        rc, out, err = remote._run_ssh('example.test', 22, 'test', '', 'bootstrap', 1, stdin_data=b'payload')
    assert rc == 124 and not out and 'completion is unknown' in err
    channel.recv_exit_status.assert_not_called()
    channel.close.assert_called_once()
