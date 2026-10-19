"""节点启动与热更新: 中断恢复、缓存失效、重启交接及计划任务迁移。"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import urllib.error
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib import windows_node_agent as agent

NAMES = agent.ASSETS + ('windows_desktop_worker.py',)
CONFIG = {'id': 'a' * 32}


def old_files(root):
    old = {name: b'# original ' + name.encode() for name in NAMES}
    for name, data in old.items():
        (root / name).write_bytes(data)
    return old


def serve(config, path, data=None):
    return b'# replacement ' + path.encode()


@pytest.mark.parametrize('victim', NAMES)
def test_replacement_failure_restores_every_original(tmp_path, victim):
    old = old_files(tmp_path)
    real_replace = os.replace
    failed = False

    def replace(source, target):
        nonlocal failed
        if not failed and Path(source) == tmp_path / '.update' / victim:
            failed = True
            raise PermissionError('file in use')
        return real_replace(source, target)

    with patch.object(agent, 'request', side_effect=serve), patch.object(agent.os, 'replace', side_effect=replace):
        with pytest.raises(PermissionError, match='file in use'):
            agent.load_assets({}, tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in NAMES} == old
    assert not (tmp_path / '.update').exists()


def test_first_install_failure_removes_partial_components(tmp_path):
    real_replace = os.replace

    def replace(source, target):
        if Path(source) == tmp_path / '.update' / agent.ASSETS[1]:
            raise PermissionError('file in use')
        return real_replace(source, target)

    with patch.object(agent, 'request', side_effect=serve), patch.object(agent.os, 'replace', side_effect=replace):
        with pytest.raises(PermissionError):
            agent.load_assets({}, tmp_path)
    assert not any((tmp_path / name).exists() for name in NAMES)


def interrupt_update(root):
    # 真正退出子进程, 不经过 except/finally, 验证重启时靠磁盘恢复而非内存撤销。
    script = f"""
import os, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from lib import windows_node_agent as agent
root = Path({str(root)!r})
agent.request = lambda config, path: b'# replacement ' + path.encode()
original = os.replace
def replace(source, target):
    if Path(source) == root / '.update' / agent.ASSETS[2]:
        os._exit(23)
    return original(source, target)
agent.os.replace = replace
agent.load_assets({{}}, root)
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=10)
    assert result.returncode == 23, result.stderr.decode()


def test_process_crash_recovers_before_contacting_server(tmp_path):
    old = old_files(tmp_path)
    interrupt_update(tmp_path)
    assert (tmp_path / 'worker.py').read_bytes() != old['worker.py']

    def offline(config, path, data=None):
        assert {name: (tmp_path / name).read_bytes() for name in NAMES} == old
        raise OSError('offline')

    with patch.object(agent, 'request', side_effect=offline):
        with pytest.raises(OSError, match='offline'):
            agent.startup_assets({}, tmp_path)
    assert not (tmp_path / '.update').exists()


def test_failed_recovery_keeps_backups_for_next_attempt(tmp_path):
    old = old_files(tmp_path)
    interrupt_update(tmp_path)
    with patch.object(agent, 'atomic_bytes', side_effect=PermissionError('still locked')):
        with pytest.raises(PermissionError):
            agent.recover_assets(tmp_path)
    assert (tmp_path / '.update' / 'transaction.json').is_file()
    assert (tmp_path / '.update' / 'backup' / 'worker.py').read_bytes() == old['worker.py']
    agent.recover_assets(tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in NAMES} == old


def test_syntax_error_never_replaces_working_components(tmp_path):
    old = old_files(tmp_path)
    with patch.object(agent, 'request', return_value=b'def broken(:\n'):
        with pytest.raises(ValueError, match='语法'):
            agent.load_assets({}, tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in NAMES} == old


def test_interrupted_cleanup_does_not_roll_back_committed_update(tmp_path):
    old_files(tmp_path)

    def interrupted_cleanup(folder, **kwargs):
        # 模拟清理只删掉 committed 就被打断; 下次启动仍必须保留新版。
        (Path(folder) / 'committed').unlink(missing_ok=True)

    with patch.object(agent, 'request', side_effect=serve), patch.object(shutil, 'rmtree', side_effect=interrupted_cleanup):
        agent.load_assets({}, tmp_path)
    assert (tmp_path / '.update').exists()
    assert not (tmp_path / '.update' / 'transaction.json').exists()
    agent.recover_assets(tmp_path)
    for name in NAMES:
        served_name = 'worker.py' if name == 'windows_desktop_worker.py' else name
        expected = serve({}, '/assets/' + served_name)
        assert (tmp_path / name).read_bytes() == agent._disk_bytes(name, expected)


def test_same_size_same_timestamp_update_uses_new_code(tmp_path, monkeypatch):
    module = 'windows_transfer'
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, module, raising=False)
    source = tmp_path / (module + '.py')
    source.write_text('VALUE = 1\n')
    timestamp = int(source.stat().st_mtime)
    os.utime(source, (timestamp, timestamp))
    assert importlib.import_module(module).VALUE == 1
    assert list((tmp_path / '__pycache__').glob(module + '.*.pyc'))
    with patch.object(agent, 'request', side_effect=lambda c, p: b'VALUE = 2\n' if p.endswith(module + '.py') else serve(c, p)):
        agent.load_assets({}, tmp_path)
    os.utime(source, (timestamp, timestamp))
    assert importlib.import_module(module).VALUE == 2
    sys.modules.pop(module, None)


def replacement_script(root, new=b'# updated agent'):
    (root / 'agent.py').write_bytes(b'# original agent')
    with patch.object(agent, 'request', return_value=new), patch.object(agent, 'powershell') as launcher:
        launcher.return_value.returncode = 0
        assert agent.update_agent(CONFIG, root, hashlib.sha256(new).hexdigest()[:16])
    return (root / 'update-restart.ps1').read_text(encoding='utf-8-sig')


def test_agent_syntax_error_keeps_original_and_does_not_restart(tmp_path):
    original = b'# original agent'
    (tmp_path / 'agent.py').write_bytes(original)
    new = b'def broken(:\n'
    with patch.object(agent, 'request', return_value=new), patch.object(agent.subprocess, 'Popen') as popen:
        assert not agent.update_agent(CONFIG, tmp_path, hashlib.sha256(new).hexdigest()[:16])
    assert (tmp_path / 'agent.py').read_bytes() == original
    popen.assert_not_called()


def test_restart_spawn_failure_restores_original_agent(tmp_path):
    original, new = b'# original agent', b'# replacement agent'
    (tmp_path / 'agent.py').write_bytes(original)
    with patch.object(agent, 'request', return_value=new), patch.object(agent, 'powershell', side_effect=OSError('launch failed')):
        with pytest.raises(OSError, match='launch failed'):
            agent.update_agent(CONFIG, tmp_path, hashlib.sha256(new).hexdigest()[:16])
    assert (tmp_path / 'agent.py').read_bytes() == original
    assert not (tmp_path / 'update-restart.ps1').exists()


def test_restart_task_registration_failure_restores_original_agent(tmp_path):
    original, new = b'# original agent', b'# replacement agent'
    (tmp_path / 'agent.py').write_bytes(original)
    with patch.object(agent, 'request', return_value=new), patch.object(agent, 'powershell') as launcher:
        launcher.return_value.returncode = 1
        with pytest.raises(RuntimeError, match='独立更新任务'):
            agent.update_agent(CONFIG, tmp_path, hashlib.sha256(new).hexdigest()[:16])
    assert (tmp_path / 'agent.py').read_bytes() == original
    assert not (tmp_path / 'update-restart.ps1').exists()


def test_update_waits_until_job_result_is_acknowledged(tmp_path):
    (tmp_path / 'agent.py').write_bytes(b'# original agent')
    new = b'# replacement agent'
    target = hashlib.sha256(new).hexdigest()[:16]
    calls = []
    polls = 0

    def request(config, path, data=None):
        nonlocal polls
        calls.append(path)
        if path == '/poll':
            polls += 1
            job = {'id': 'job-a', 'action': 'screenshot'} if polls == 1 else None
            return json.dumps({'job': job, 'versions': {'agent': target}}).encode()
        if path == '/result/job-a':
            if calls.count(path) == 1:
                raise urllib.error.URLError('result connection lost')
            return b'{}'
        if path == '/assets/agent.py':
            return new
        raise AssertionError(path)

    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value=CONFIG), \
         patch.object(agent, 'startup_assets', return_value=None), \
         patch.object(agent, 'harden_task'), patch.object(agent.os, 'chdir'), \
         patch.object(agent.time, 'sleep'), patch.object(agent, 'request', side_effect=request), \
         patch.object(agent, 'run_job', return_value={'result': {'ok': True}}) as run_job, \
         patch.object(agent, 'powershell') as launcher:
        launcher.return_value.returncode = 0
        agent.main()
    run_job.assert_called_once()
    assert calls == ['/poll', '/result/job-a', '/result/job-a', '/poll', '/assets/agent.py']


def test_running_agent_rechecks_startup_configuration_hourly(tmp_path):
    clock = 0
    polls = 0

    def request(config, path, data=None):
        nonlocal clock, polls
        assert path == '/poll'
        polls += 1
        if polls == 2:
            raise KeyboardInterrupt
        clock = agent.TASK_REPAIR_INTERVAL + 1
        return b'{"job": null}'

    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value=CONFIG), \
         patch.object(agent, 'startup_assets', return_value=None), \
         patch.object(agent.os, 'chdir'), patch.object(agent, 'request', side_effect=request), \
         patch.object(agent.time, 'monotonic', side_effect=lambda: clock), \
         patch.object(agent, 'harden_task') as repair:
        with pytest.raises(KeyboardInterrupt):
            agent.main()
    assert repair.call_count == 2


def test_agent_reconnects_after_an_hour_without_reenrollment(tmp_path):
    clock = 0
    waits = []
    completed = []
    received_job = False

    def sleep(delay):
        nonlocal clock
        waits.append(delay)
        clock += delay

    def request(config, path, data=None):
        nonlocal received_job
        assert config is CONFIG
        if clock < 3600:
            raise urllib.error.URLError('network unavailable')
        if path == '/result/after-reconnect':
            completed.append(data)
            return b'{}'
        assert path == '/poll'
        if received_job:
            raise KeyboardInterrupt
        received_job = True
        return json.dumps({'job': {'id': 'after-reconnect', 'action': 'system_info'}}).encode()

    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value=CONFIG) as load, \
         patch.object(agent, 'startup_assets', return_value=None), \
         patch.object(agent.os, 'chdir'), patch.object(agent, 'request', side_effect=request), \
         patch.object(agent.time, 'monotonic', side_effect=lambda: clock), \
         patch.object(agent.time, 'sleep', side_effect=sleep), \
         patch.object(agent, 'harden_task'), patch.object(agent, 'disable_task') as disable, \
         patch.object(agent, 'run_job', return_value={'result': {'ok': True}}) as run:
        with pytest.raises(KeyboardInterrupt):
            agent.main()
    assert clock >= 3600 and max(waits) <= 60
    assert completed == [{'result': {'ok': True}}]
    load.assert_called_once()
    run.assert_called_once()
    disable.assert_not_called()


@pytest.mark.parametrize('fails', [False, True])
def test_long_job_keeps_heartbeat_and_stops_it_after_completion(tmp_path, fails):
    seen = threading.Event()
    calls = []
    expected = {'result': {'ok': True}}

    def request(config, path, data=None):
        calls.append((path, data))
        seen.set()

    def run(*args):
        assert seen.wait(2), '任务执行期间未回报心跳'
        if fails:
            raise ValueError('worker failed')
        return expected

    with patch.object(agent, 'JOB_HEARTBEAT_INTERVAL', 0.01), \
         patch.object(agent, 'request', side_effect=request), \
         patch.object(agent, 'run_job', side_effect=run) as worker:
        if fails:
            with pytest.raises(ValueError, match='worker failed'):
                agent.run_with_heartbeat(CONFIG, tmp_path, {'id': 'long'}, {})
        else:
            assert agent.run_with_heartbeat(CONFIG, tmp_path, {'id': 'long'}, {}) is expected
    worker.assert_called_once()
    assert calls and all(path == '/status' and data == {'state': 'online'} for path, data in calls)
    assert not any(t.name == 'node-job-heartbeat' for t in threading.enumerate())


def run_ps(tmp_path, script):
    executable = shutil.which('pwsh')
    if not executable:
        pytest.skip('未安装 PowerShell; Python 侧恢复用例仍可运行')
    path = tmp_path / 'test.ps1'
    path.write_text(script, encoding='utf-8-sig')
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-File', str(path)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_restart_waits_for_process_and_task_to_exit(tmp_path):
    script = replacement_script(tmp_path)
    # 模拟进程已退出但任务仍在 Running 两次, 如果提前启动就报错。
    harness = """
$script:waited=$false; $script:checks=0
function Get-Process { $p=New-Object PSObject; $p | Add-Member ScriptMethod WaitForExit {$script:waited=$true}; return $p }
function Get-ScheduledTask {
  if(-not $script:waited){throw 'process not waited'}
  $script:checks++; @{State=$(if($script:checks -lt 3){'Running'}else{'Ready'})}
}
function Start-ScheduledTask {
  if($script:checks -lt 3){throw 'started before old task exited'}
  @{waited=$script:waited;checks=$script:checks} | ConvertTo-Json -Compress
}
function Start-Sleep {}
function Unregister-ScheduledTask {}
"""
    assert run_ps(tmp_path, harness + script) == {'waited': True, 'checks': 3}


def test_restart_helper_is_registered_in_an_independent_task(tmp_path):
    (tmp_path / 'agent.py').write_bytes(b'# original agent')
    new = b'# replacement agent'
    with patch.object(agent, 'request', return_value=new), patch.object(agent, 'powershell') as launcher:
        launcher.return_value.returncode = 0
        assert agent.update_agent(CONFIG, tmp_path, hashlib.sha256(new).hexdigest()[:16])
    script = launcher.call_args.args[0].replace('[Security.Principal.WindowsIdentity]::GetCurrent().Name', "'LAB\\installer'")
    harness = """
function New-ScheduledTaskAction {param($Execute,$Argument,$WorkingDirectory) @{args=$Argument;cwd=$WorkingDirectory}}
function New-ScheduledTaskPrincipal {param($UserId,$LogonType,$RunLevel) @{user=$UserId}}
function New-ScheduledTaskSettingsSet {param($ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,$MultipleInstances) @{}}
function Register-ScheduledTask {param($TaskName,$Action,$Principal,$Settings,[switch]$Force) $script:registered=@{name=$TaskName;action=$Action}}
function Start-ScheduledTask {
  param($TaskName)
  if($script:registered.name -ne $TaskName){throw 'unregistered task'}
  $script:registered | ConvertTo-Json -Depth 4 -Compress
}
"""
    result = run_ps(tmp_path, harness + script)
    assert result['name'] == 'LiteCode-Update-' + CONFIG['id']
    assert str(tmp_path / 'update-restart.ps1') in result['action']['args']
    assert (tmp_path / 'update-restart.ps1').is_file()


@pytest.mark.parametrize('state', ['existing', 'missing', 'disabled'])
def test_startup_task_migrates_or_recreates_and_respects_disabled(tmp_path, state):
    root = tmp_path / "node user's folder"
    root.mkdir()
    with patch.object(agent, 'powershell') as shell:
        shell.return_value.returncode = 0
        agent.harden_task(CONFIG, root)
    script = shell.call_args.args[0].replace('[Security.Principal.WindowsIdentity]::GetCurrent().Name', "'LAB\\installer'")
    task = '$null' if state == 'missing' else ('@{Settings=@{Enabled=$false}}' if state == 'disabled' else '@{Settings=@{Enabled=$true}}')
    harness = f"function Get-ScheduledTask {{ {task} }}\n" + """
$script:changed=$false
function New-ScheduledTaskAction {param($Execute,$Argument,$WorkingDirectory) @{execute=$Execute;args=$Argument;cwd=$WorkingDirectory}}
function New-ScheduledTaskPrincipal {param($UserId,$LogonType,$RunLevel) @{user=$UserId;logon=$LogonType}}
function New-ScheduledTaskSettingsSet {param($RestartCount,$RestartInterval,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,$MultipleInstances,[switch]$StartWhenAvailable) @{restarts=$RestartCount;instances=$MultipleInstances}}
function New-ScheduledTaskTrigger {param([switch]$AtStartup,[switch]$AtLogOn,$User,[switch]$Once,$At,$RepetitionInterval) @{boot=[bool]$AtStartup;login=[bool]$AtLogOn;repeat=($RepetitionInterval.TotalMinutes)}}
function Set-ScheduledTask {param($TaskName,$Action,$Principal,$Settings,$Trigger) $script:changed=@{kind='set';action=$Action;principal=$Principal;settings=$Settings;triggers=$Trigger}}
function Register-ScheduledTask {param($TaskName,$Action,$Principal,$Settings,$Trigger) $script:changed=@{kind='register';action=$Action;principal=$Principal;settings=$Settings;triggers=$Trigger}}
"""
    result = run_ps(tmp_path, harness + '& { ' + script + ' }; $script:changed | ConvertTo-Json -Depth 8 -Compress')
    if state == 'disabled':
        assert result is False
        return
    assert result['kind'] == ('set' if state == 'existing' else 'register')
    assert result['action']['cwd'] == str(root)
    assert str(root / 'agent.py') in result['action']['args']
    assert result['principal']['logon'] == 'Interactive'
    assert result['settings'] == {'restarts': 999, 'instances': 'IgnoreNew'}
    assert len(result['triggers']) == 3
    assert any(t['boot'] for t in result['triggers']) and any(t['login'] for t in result['triggers'])
    assert any(t['repeat'] == 1 for t in result['triggers'])
