"""[2026-10-19] SSH 工具空转修复回归 (全本地, 假 paramiko 对象, 不连任何主机)。"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path[:0] = [_ROOT, os.path.join(_ROOT, 'core')]

from core.tools.handlers import remote  # noqa: E402

CONN = {'host': 'example.test', 'port': 22, 'user': 'root', 'password': 'x', 'key_file': '', 'jump': None}


def _client(channel, stdin=None):
    client = MagicMock()
    client.exec_command.return_value = (stdin, SimpleNamespace(channel=channel), None)
    return client


def _done_channel(rc=0):
    ch = MagicMock()
    ch.exit_status_ready.return_value = True
    ch.recv_ready.return_value = False
    ch.recv_stderr_ready.return_value = False
    ch.recv_exit_status.return_value = rc
    return ch


# ---- 1. 无 stdin_data 时关闭写端 ----
def test_no_stdin_data_shuts_down_write():
    ch = _done_channel()
    remote._read_ssh_command(_client(ch), 'cat', 10)
    ch.shutdown_write.assert_called_once()


def test_stdin_data_path_does_not_shutdown_before_write():
    ch = _done_channel()
    stdin = MagicMock()
    remote._read_ssh_command(_client(ch, stdin), 'x', 10, stdin_data=b'abc')
    ch.shutdown_write.assert_not_called()
    stdin.write.assert_called_once_with(b'abc')
    stdin.close.assert_called_once()


# ---- 2. 无输出卡死提前返回 ----
class _Clock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def test_stall_returns_early_with_hint():
    ch = MagicMock()
    ch.exit_status_ready.return_value = False
    ch.recv_ready.return_value = False
    ch.recv_stderr_ready.return_value = False
    clock = _Clock(10.0)
    with patch.object(remote.time, 'monotonic', clock), patch.object(remote.time, 'sleep'):
        rc, out, err = remote._read_ssh_command(_client(ch), 'docker pull -q x', 900)
    assert rc == 124 and 'no output for 300s' in err and 'nohup' in err
    assert clock.t < 900          # 没白等满 900s
    ch.close.assert_called_once()
    ch.recv_exit_status.assert_not_called()


def test_steady_output_is_not_stalled():
    ch = MagicMock()
    ticks = {'n': 0}

    def ready():
        ticks['n'] += 1
        return ticks['n'] > 200   # 跑 ~2000s 虚拟时间, 一直有输出

    ch.exit_status_ready.side_effect = ready
    ch.recv_ready.return_value = True
    ch.recv.return_value = b'.'
    ch.recv_stderr_ready.return_value = False
    ch.recv_exit_status.return_value = 0
    with patch.object(remote.time, 'monotonic', _Clock(10.0)), patch.object(remote.time, 'sleep'):
        # recv_ready 恒 True, 排干循环靠 recv 次数上限终止
        ch.recv_ready.side_effect = [True] * 400 + [False] * 1000
        rc, out, err = remote._read_ssh_command(_client(ch), 'build', 5000)
    assert rc == 0 and 'stalled' not in err


def test_short_timeout_never_uses_stall_window():
    assert remote._stall_window(60) >= 60
    assert remote._stall_window(120) >= 120
    assert remote._stall_window(900) == 300


# ---- 3. 握手失败分类 + 短重试 ----
def test_not_ready_classification():
    assert remote._is_not_ready(Exception('Error reading SSH protocol banner'))
    assert remote._is_not_ready(ConnectionRefusedError())
    assert remote._is_not_ready(ConnectionResetError())
    assert remote._is_not_ready(type('NoValidConnectionsError', (OSError,), {})())
    assert not remote._is_not_ready(type('AuthenticationException', (Exception,), {})('Authentication failed'))
    assert not remote._is_not_ready(ValueError('bad'))


def test_connect_retries_then_succeeds():
    calls = []

    def once(conn):
        calls.append(1)
        if len(calls) < 3:
            raise Exception('Error reading SSH protocol banner')
        return 'client', None

    with patch.object(remote, '_connect_once', once), patch.object(remote.time, 'sleep') as sl:
        assert remote._connect(CONN) == ('client', None)
    assert len(calls) == 3 and sl.call_count == 2


def test_connect_gives_up_with_host_not_ready():
    clock = _Clock(0.0)
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        clock.t += s

    with patch.object(remote, '_connect_once', side_effect=ConnectionRefusedError('refused')), \
         patch.object(remote.time, 'monotonic', clock), patch.object(remote.time, 'sleep', fake_sleep):
        with pytest.raises(remote.HostNotReady) as ei:
            remote._connect(CONN)
    assert '还没就绪' in str(ei.value) and 'example.test' in str(ei.value)
    assert sum(sleeps) <= remote._CONNECT_RETRY_BUDGET


def test_auth_failure_is_not_retried():
    err = type('AuthenticationException', (Exception,), {})('Authentication failed.')
    with patch.object(remote, '_connect_once', side_effect=err) as once, patch.object(remote.time, 'sleep') as sl:
        with pytest.raises(Exception):
            remote._connect(CONN)
    assert once.call_count == 1 and sl.call_count == 0


def test_ssh_exec_host_not_ready_message():
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock(side_effect=remote.HostNotReady('主机 example.test:22 还没就绪 (SSH 握手失败, 可能在重启中)'))):
        text, _ = asyncio.run(remote.h_ssh_exec('s', {'cmd': 'uptime'}))
    assert text.startswith('ERROR: 主机 example.test:22 还没就绪')


# ---- 4. docker_remote_recreate pipefail + -d ----
def _recreate(args, rc=0):
    ssh = AsyncMock(return_value=(rc, 'out', 'err'))
    with patch.object(remote, '_conn', return_value=(CONN, '')), patch.object(remote, '_ssh_conn', new=ssh):
        text, _ = asyncio.run(remote.h_docker_remote_recreate('s', {'container': 'c', 'image': 'img', **args}))
    return text, ssh.call_args.args[1]


def test_recreate_uses_pipefail_and_reports_error():
    text, cmd = _recreate({'run_flags': '-d --name c'}, rc=125)
    assert cmd.startswith('bash -c ') and 'pipefail' in cmd
    assert text.startswith('ERROR: docker_remote_recreate rc=125')
    text, _ = _recreate({'run_flags': '-d --name c'}, rc=0)
    assert not text.startswith('ERROR')


def test_recreate_adds_detach():
    _, cmd = _recreate({'run_flags': '--name c -p 80:80'})
    assert 'docker run -d --name c' in cmd
    _, cmd = _recreate({'run_flags': '-itd --name c'})
    assert 'docker run -itd --name c' in cmd and 'run -d -itd' not in cmd
    assert remote._has_detach('--detach --rm') and not remote._has_detach('-e DEBUG=1 --rm')


def test_recreate_script_real_bash_pipefail(tmp_path):
    """真 bash 跑生成脚本 (docker 换成假命令): run 失败 → rc≠0; stop/rm 失败容忍。"""
    import shutil
    import subprocess
    if not shutil.which('bash'):
        pytest.skip('no bash')
    fake = tmp_path / 'docker'
    fake.write_text('#!/bin/sh\ncase "$1" in run) echo "run boom" >&2; exit 125;; stop|rm) exit 1;; esac\nexit 0\n')
    fake.chmod(0o755)
    _, cmd = _recreate({'run_flags': '--name c'})
    env = {**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}'}
    cmd = cmd.replace('sleep 3', 'true')
    p = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == 125 and 'run boom' in p.stdout and '(stop skipped)' in p.stdout


# ---- 5. 截断保头+尾 ----
def test_clip_keeps_tail():
    s = 'HEAD' + 'x' * 10000 + 'FINAL_ERROR'
    c = remote._clip(s, 4000)
    assert c.startswith('HEAD') and c.endswith('FINAL_ERROR') and 'truncated' in c
    assert remote._clip('short', 4000) == 'short'


def test_ssh_exec_output_keeps_final_error():
    out = 'line\n' * 5000 + 'FATAL: final error'
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock(return_value=(1, out, 'e' * 5000 + 'LAST_ERR'))):
        text, _ = asyncio.run(remote.h_ssh_exec('s', {'cmd': 'make'}))
    assert 'FATAL: final error' in text and 'LAST_ERR' in text


# ---- 6/7. 假 SFTP ----
class FakeSftp:
    def __init__(self, files, modes):
        self.files, self.modes, self.owner = files, modes, {}

    def stat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_size=len(self.files[path]), st_mode=0o100000 | self.modes[path], st_uid=1000, st_gid=1000)

    def rename(self, a, b):
        self.files[b] = self.files.pop(a)
        self.modes[b] = self.modes.pop(a)

    def open(self, path, mode):
        sftp = self

        class F:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                data = sftp.files[path]
                return data if n < 0 else data[:n]

            def write(self, content):
                sftp.files[path] = content.encode() if isinstance(content, str) else content
                sftp.modes[path] = 0o644            # SFTP 新建文件默认 0644

        return F()

    def chmod(self, path, mode):
        self.modes[path] = mode

    def chown(self, path, uid, gid):
        self.owner[path] = (uid, gid)

    def close(self):
        pass


def _with_sftp(sftp):
    client = MagicMock()
    client.open_sftp.return_value = sftp
    return patch.object(remote, '_connect', return_value=(client, None))


def test_read_file_cjk_truncation_flagged():
    data = ('中文内容' * 5000).encode()           # 60KB, 20000 字
    sftp = FakeSftp({'/a.md': data}, {'/a.md': 0o644})
    with _with_sftp(sftp), patch.object(remote, '_conn', return_value=(CONN, '')):
        text, _ = asyncio.run(remote.h_ssh_read_file('s', {'path': '/a.md', 'max_chars': 20000}))
    assert text.endswith('...(truncated)')


def test_read_file_small_not_truncated():
    sftp = FakeSftp({'/a.md': '你好'.encode()}, {'/a.md': 0o644})
    with _with_sftp(sftp), patch.object(remote, '_conn', return_value=(CONN, '')):
        text, _ = asyncio.run(remote.h_ssh_read_file('s', {'path': '/a.md'}))
    assert '你好' in text and 'truncated' not in text


def test_write_file_preserves_exec_bit():
    sftp = FakeSftp({'/d.sh': b'echo 1'}, {'/d.sh': 0o755})
    with _with_sftp(sftp), patch.object(remote, '_conn', return_value=(CONN, '')):
        text, _ = asyncio.run(remote.h_ssh_write_file('s', {'path': '/d.sh', 'content': 'echo 2'}))
    assert text.startswith('[sftp') and sftp.modes['/d.sh'] == 0o755 and sftp.owner['/d.sh'] == (1000, 1000)
    assert sftp.files['/d.sh.bak'] == b'echo 1'


def test_str_replace_and_sync_preserve_exec_bit():
    sftp = FakeSftp({'/d.sh': b'echo 1', '/e.sh': b'x'}, {'/d.sh': 0o755, '/e.sh': 0o750})
    with _with_sftp(sftp), patch.object(remote, '_conn', return_value=(CONN, '')):
        asyncio.run(remote.h_ssh_str_replace('s', {'path': '/d.sh', 'old_str': '1', 'new_str': '2'}))
        asyncio.run(remote.h_ssh_sync_files('s', {'files': {'/e.sh': 'y', '/new.txt': 'n'}}))
    assert sftp.files['/d.sh'] == b'echo 2' and sftp.modes['/d.sh'] == 0o755
    assert sftp.modes['/e.sh'] == 0o750 and sftp.modes['/new.txt'] == 0o644
