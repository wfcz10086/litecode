"""[2026-10-19] Windows 节点端审计修复: 自更新回滚、重启助手兜底、SSH 归属登记与撤销、恢复失败仍轮询、任务总时限。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib import windows_node_agent as agent
from tests.test_windows_node_lifecycle import run_ps, replacement_script
from tests.test_windows_ssh_bootstrap import ssh_prelude, ssh_block

CONFIG = {'id': 'a' * 32}
TAG = 'litecode-node-' + 'a' * 32


@pytest.fixture(autouse=True)
def clean_health():
    agent.HEALTH.clear()
    yield
    agent.HEALTH.clear()


# ---------------- 1. 自更新回滚 ----------------

def _start(root):
    return subprocess.run([sys.executable, str(root / agent.LAUNCHER_FILE), str(root)], capture_output=True, timeout=20)


def test_launcher_rolls_back_agent_that_crashes_on_every_start(tmp_path):
    good = b'from pathlib import Path\nimport sys\nPath(sys.argv[1], "ran.txt").write_text("old")\n'
    bad = b'raise SystemExit("new agent crashes at startup")\n'
    (tmp_path / 'agent.py').write_bytes(good)
    agent.ensure_launcher(tmp_path)
    with patch.object(agent, 'request', return_value=bad), patch.object(agent, 'powershell') as shell:
        shell.return_value.returncode = 0
        assert agent.update_agent(CONFIG, tmp_path, hashlib.sha256(bad).hexdigest()[:16])
    pending = json.loads((tmp_path / agent.PENDING_FILE).read_text())
    assert pending['version'] == hashlib.sha256(bad).hexdigest()[:16] and pending['attempts'] == 0
    for attempt in range(agent.ROLLBACK_AFTER):      # 前 3 次启动: 运行新代理 (崩溃), 计数
        assert _start(tmp_path).returncode != 0
        assert json.loads((tmp_path / agent.PENDING_FILE).read_text())['attempts'] == attempt + 1
    assert _start(tmp_path).returncode == 0           # 第 4 次: 先换回旧代理再运行
    assert (tmp_path / 'agent.py').read_bytes() == good and (tmp_path / 'ran.txt').read_text() == 'old'
    assert not (tmp_path / agent.PENDING_FILE).exists()
    record = json.loads((tmp_path / agent.ROLLBACK_FILE).read_text())
    assert record['bad_version'] == pending['version']


def test_launcher_ignores_stale_record_and_healthy_agent_clears_it(tmp_path):
    (tmp_path / 'agent.py').write_bytes(b'pass\n')
    (tmp_path / 'agent.py.previous').write_bytes(b'raise SystemExit(9)\n')
    agent.ensure_launcher(tmp_path)
    (tmp_path / agent.PENDING_FILE).write_text(json.dumps({'version': 'f' * 16, 'attempts': 99}))
    assert _start(tmp_path).returncode == 0           # 记录与磁盘代理不符: 不回滚, 只清理
    assert (tmp_path / 'agent.py').read_bytes() == b'pass\n' and not (tmp_path / agent.PENDING_FILE).exists()


def test_failed_restart_scheduling_drops_pending_record(tmp_path):
    (tmp_path / 'agent.py').write_bytes(b'# original')
    new = b'# new'
    with patch.object(agent, 'request', return_value=new), patch.object(agent, 'powershell', side_effect=OSError('x')):
        with pytest.raises(OSError):
            agent.update_agent(CONFIG, tmp_path, hashlib.sha256(new).hexdigest()[:16])
    assert (tmp_path / 'agent.py').read_bytes() == b'# original' and not (tmp_path / agent.PENDING_FILE).exists()


def _main(tmp_path, polls, served=lambda path: b'{}', **extra):
    calls = []

    def response(config, path, data=None):
        calls.append((path, data, dict(agent.HEALTH)))
        if path == '/poll':
            if not polls:
                raise KeyboardInterrupt
            return json.dumps(polls.pop(0)).encode()
        return served(path)
    patches = [patch.object(sys, 'argv', ['agent.py', str(tmp_path)]),
               patch.object(agent, 'load_config', return_value=CONFIG),
               patch.object(agent, 'request', side_effect=response),
               patch.object(agent, 'harden_task'), patch.object(agent.os, 'chdir'),
               patch.object(agent.time, 'sleep')]
    patches += [patch.object(agent, name, value) for name, value in extra.items()]
    for p in patches:
        p.start()
    try:
        try:
            agent.main()
        except KeyboardInterrupt:
            pass
    finally:
        for p in reversed(patches):
            p.stop()
    return calls


def test_first_successful_poll_marks_healthy_and_rollback_is_reported_once(tmp_path):
    (tmp_path / agent.PENDING_FILE).write_text('{"version": "x", "attempts": 1}')
    bad = 'b' * 16
    (tmp_path / agent.ROLLBACK_FILE).write_text(json.dumps({'bad_version': bad, 'attempts': 3}))
    idle = {'job': None, 'versions': {'agent': bad}}
    calls = _main(tmp_path, [idle, dict(idle)], startup_assets=lambda c, r: None)
    assert not (tmp_path / agent.PENDING_FILE).exists()
    statuses = [data for path, data, _ in calls if path == '/status']
    assert len(statuses) == 1 and statuses[0]['state'] == 'error' and bad in statuses[0]['error']
    assert '/assets/agent.py' not in [c[0] for c in calls]          # 不再自动升级到刚回滚掉的坏版本
    assert all(h.get('rollback') == 'agent_rolled_back' for _, _, h in calls)
    assert json.loads((tmp_path / agent.ROLLBACK_FILE).read_text())['reported'] is True
    assert (tmp_path / agent.LAUNCHER_FILE).read_text() == agent.LAUNCHER_SOURCE


def test_harden_task_points_scheduled_task_at_launcher_with_highest_privilege(tmp_path):
    agent.ensure_launcher(tmp_path)
    with patch.object(agent, 'powershell') as shell:
        agent.harden_task(CONFIG, tmp_path)
    script = shell.call_args.args[0]
    assert str(tmp_path / agent.LAUNCHER_FILE) in script and '-RunLevel Highest' in script


# ---------------- 2. 重启助手: 任务一直 Running 时 Stop 再 Start ----------------

def test_restart_helper_stops_and_starts_task_still_running_after_wait(tmp_path):
    script = replacement_script(tmp_path)
    harness = """
$script:calls=@()
function Get-Process { $p=New-Object PSObject; $p | Add-Member ScriptMethod WaitForExit {}; return $p }
function Get-ScheduledTask { @{State=$(if($script:calls -contains 'stop'){'Ready'}else{'Running'})} }
function Stop-ScheduledTask {param($TaskName) $script:calls+='stop'; $script:stopped=$TaskName}
function Start-ScheduledTask {param($TaskName) $script:calls+='start'; $script:started=$TaskName}
function Start-Sleep {}
function Unregister-ScheduledTask {param($TaskName) $script:removed=$TaskName}
"""
    result = run_ps(tmp_path, harness + '& { ' + script + ' }; @{calls=$script:calls;stopped=$script:stopped;'
                    'started=$script:started;removed=$script:removed} | ConvertTo-Json -Compress')
    assert result['calls'] == ['stop', 'start']
    assert result['stopped'] == result['started'] == 'LiteCode-Node-' + CONFIG['id']   # 只动本节点自己的任务
    assert result['removed'] == 'LiteCode-Update-' + CONFIG['id']
    assert '强制停止' in (tmp_path / 'update-restart.log').read_text(encoding='utf-8-sig')


def test_restart_helper_does_not_throw_away_relaunch_any_more():
    with patch.object(agent, 'powershell') as shell, patch.object(agent, 'atomic_bytes') as write:
        shell.return_value.returncode = 0
        agent.schedule_restart(CONFIG, Path('/x'))
    script = write.call_args.args[1].decode('utf-8-sig')
    assert "throw '代理重启失败" not in script
    assert script.index('Stop-ScheduledTask') < script.rindex('Start-ScheduledTask')


# ---------------- 3/4. SSH 归属登记、卸载恢复、安装失败撤销 ----------------

def test_undo_function_is_identical_in_bootstrap_and_uninstall():
    def block(name):
        text = (ROOT / 'lib' / name).read_text()
        return text[text.index('# >>> LiteCode SSH 撤销'):text.index('# <<< LiteCode SSH 撤销')]
    assert block('windows_node_bootstrap.ps1') == block('windows_node_uninstall.ps1')


def test_uninstall_restores_ssh_before_deleting_node_folder():
    source = (ROOT / 'lib/windows_node_uninstall.ps1').read_text()
    assert source.index("Undo-LiteCodeSsh $sshOwner") < source.index('Remove-Item -LiteralPath $Root -Recurse')


SSH_HARNESS = """
$ErrorActionPreference='Stop'; $script:log=@()
$enrollment=@{id=('a'*32)}; $SshPublicKey='ssh-ed25519 AAAA litecode-node-'+$enrollment.id
function Write-Host {}
function Get-WindowsCapability {param([switch]$Online,$Name) @{Name='OpenSSH.Server~~~~0.0.1.0';State=$(if($script:capability){'Installed'}else{'NotPresent'})}}
function Add-WindowsCapability {param([switch]$Online,$Name) $script:installed=$true; $script:capability=$true; $script:log+='add-capability'}
function Remove-WindowsCapability {param([switch]$Online,$Name) $script:log+=('remove-capability:'+$Name)}
function Get-NetFirewallRule {param($Name) if($Name -eq 'OpenSSH-Server-In-TCP' -and $script:installed){@{Name=$Name;Enabled='True'}}}
function Disable-NetFirewallRule {$script:log+='disable-builtin'}
function New-NetFirewallRule {param($Name,$DisplayName,$Direction,$Protocol,$LocalPort,$Action,$RemoteAddress) $script:log+=('new-rule:'+$Name)}
function Remove-NetFirewallRule {param($Name) $script:log+=('remove-rule:'+$Name)}
function Set-Service {param($Name,$StartupType) $script:log+=('startup:'+$StartupType)}
function Start-Service {param($Name) $script:log+='start'; $script:status='Running'}
function Stop-Service {param($Name,[switch]$Force) $script:log+='stop'; $script:status='Stopped'}
function Restart-Service {param($Name) $script:log+='restart'}
function Get-Service {param($Name) if($script:installed){@{Status=$script:status;StartType=$script:startType}}}
function icacls.exe {$global:LASTEXITCODE=0}
"""


def _ssh_env(tmp_path, keys='ssh-ed25519 AAAA existing-user\n'):
    program = tmp_path / 'Program Data'
    (program / 'ssh').mkdir(parents=True)
    (program / 'ssh' / 'sshd_config').write_text('#PasswordAuthentication yes\nSubsystem sftp sftp-server.exe\n')
    (program / 'ssh' / 'administrators_authorized_keys').write_text(keys)
    root = tmp_path / 'node'
    root.mkdir()
    return program, root


def _state(installed, capability, status='Running', start='Automatic'):
    return (f"$script:installed=${str(installed).lower()}; $script:capability=${str(capability).lower()}; "
            f"$script:status='{status}'; $script:startType='{start}';\n")


def _run_ssh(tmp_path, program, root, state, tail):
    env = "$env:ProgramData='" + str(program).replace("'", "''") + "';\n"
    return run_ps(tmp_path, env + state + SSH_HARNESS + ssh_prelude(root) + 'try {\n' + ssh_block() +
                  '} catch { throw }\n' + tail)


def test_fresh_ssh_install_is_recorded_and_fully_undone(tmp_path):
    program, root = _ssh_env(tmp_path)
    original = (program / 'ssh' / 'sshd_config').read_text()
    owner_after_install = _run_ssh(tmp_path, program, root, _state(False, False),
        "$o=Get-Content $sshOwnerFile -Raw | ConvertFrom-Json; $script:log=@(); "
        "$ok=Undo-LiteCodeSsh $o $TAG $sshBackup; @{owner=$o;ok=$ok;log=$script:log} | ConvertTo-Json -Compress -Depth 4"
        .replace('$TAG', "'" + TAG + "'"))
    owner = owner_after_install['owner']
    assert owner['capability'] == 'OpenSSH.Server~~~~0.0.1.0' and owner['firewall_rule'] is True
    assert owner['config_backup'] is True and owner['key_added'] is True and owner['builtin_rule_disabled'] is True
    assert owner_after_install['ok'] is True
    log = owner_after_install['log']
    assert 'remove-rule:LiteCode-SSH-In-TCP' in log and 'remove-capability:OpenSSH.Server~~~~0.0.1.0' in log
    keys = (program / 'ssh' / 'administrators_authorized_keys').read_text()
    assert TAG not in keys and 'existing-user' in keys
    assert (program / 'ssh' / 'sshd_config').read_text() == original


def test_preexisting_sshd_is_never_removed_only_its_state_restored(tmp_path):
    program, root = _ssh_env(tmp_path)
    original = (program / 'ssh' / 'sshd_config').read_text()
    result = _run_ssh(tmp_path, program, root, _state(True, False, status='Stopped', start='Manual'),
        "$o=Get-Content $sshOwnerFile -Raw | ConvertFrom-Json; $script:log=@(); "
        "$ok=Undo-LiteCodeSsh $o '" + TAG + "' $sshBackup; @{owner=$o;ok=$ok;log=$script:log} | ConvertTo-Json -Compress -Depth 4")
    assert result['owner']['capability'] == '' and result['owner']['config_backup'] is False
    assert result['owner']['service'] == {'start_type': 'Manual', 'status': 'Stopped'}
    assert result['log'] == ['startup:Manual', 'stop']        # 不卸载功能、不删规则, 只恢复启动类型与停止状态
    assert (program / 'ssh' / 'sshd_config').read_text() == original


def test_shared_sshd_kept_when_another_litecode_node_still_uses_it(tmp_path):
    other = 'ssh-ed25519 CCCC litecode-node-' + 'b' * 32 + '\n'
    program, root = _ssh_env(tmp_path, keys=other)
    result = _run_ssh(tmp_path, program, root, _state(False, False),
        "$o=Get-Content $sshOwnerFile -Raw | ConvertFrom-Json; $script:log=@(); "
        "$ok=Undo-LiteCodeSsh $o '" + TAG + "' $sshBackup; @{ok=$ok;log=$script:log} | ConvertTo-Json -Compress")
    assert result['ok'] is False and result['log'] in ([], None)
    keys = (program / 'ssh' / 'administrators_authorized_keys').read_text()
    assert TAG not in keys and 'b' * 32 in keys


def _catch_body():
    source = (ROOT / 'lib/windows_node_bootstrap.ps1').read_text()
    return source[source.index('    $failure = $_'):source.index('    try { Invoke-RestMethod -Method Post -Uri "$device/status"',
                                                                  source.index('    $failure = $_'))]


def test_bootstrap_failure_after_ssh_step_revokes_key_and_new_sshd(tmp_path):
    program, root = _ssh_env(tmp_path)
    original = (program / 'ssh' / 'sshd_config').read_text()
    env = "$env:ProgramData='" + str(program).replace("'", "''") + "';\n"
    script = (env + _state(False, False) + SSH_HARNESS + ssh_prelude(root) + 'try {\n' + ssh_block() +
              "    throw 'pip 安装失败'\n} catch {\n" + _catch_body() + "}\n"
              "@{log=$script:log;owner=(Test-Path $sshOwnerFile);backup=(Test-Path $sshBackup)} | ConvertTo-Json -Compress")
    result = run_ps(tmp_path, script)
    assert 'remove-capability:OpenSSH.Server~~~~0.0.1.0' in result['log'] and 'remove-rule:LiteCode-SSH-In-TCP' in result['log']
    assert result['owner'] is False and result['backup'] is False
    assert TAG not in (program / 'ssh' / 'administrators_authorized_keys').read_text()
    assert (program / 'ssh' / 'sshd_config').read_text() == original


def test_bootstrap_rerun_failure_keeps_artifacts_of_earlier_successful_install(tmp_path):
    program, root = _ssh_env(tmp_path)
    env = "$env:ProgramData='" + str(program).replace("'", "''") + "';\n"
    first = run_ps(tmp_path, env + _state(False, False) + SSH_HARNESS + ssh_prelude(root) + 'try {\n' + ssh_block() +
                   '} catch { throw }\n@{ok=$true} | ConvertTo-Json -Compress')
    assert first == {'ok': True}
    before = (root / 'ssh-owner.json').read_text(encoding='utf-8-sig')
    script = (env + _state(True, True) + SSH_HARNESS + ssh_prelude(root) + 'try {\n' + ssh_block() +
              "    throw 'later failure'\n} catch {\n" + _catch_body() + "}\n@{log=$script:log} | ConvertTo-Json -Compress")
    result = run_ps(tmp_path, script)
    assert not any(entry.startswith('remove-') for entry in (result['log'] or []))
    assert TAG in (program / 'ssh' / 'administrators_authorized_keys').read_text()
    assert json.loads((root / 'ssh-owner.json').read_text(encoding='utf-8-sig')) == json.loads(before)


# ---------------- 6. 恢复失败仍轮询 ----------------

def test_corrupt_recovery_record_keeps_polling_and_allows_uninstall_and_system_jobs(tmp_path):
    (tmp_path / '.update').mkdir()
    (tmp_path / '.update' / 'transaction.json').write_text('[]')       # 损坏的恢复记录
    polls = [{'job': {'id': 'd', 'action': 'screenshot', 'request': {}}},
             {'job': {'id': 's', 'action': 'system_info', 'request': {}}},
             {'job': {'id': 'u', 'action': 'uninstall', 'request': {}}}]
    with patch.object(agent, 'launch_uninstall') as uninstall, \
         patch.object(agent, 'run_job', return_value={'result': {'ok': True}, 'images': {}}) as run:
        calls = _main(tmp_path, polls, startup_assets=lambda c, r: pytest.fail('恢复失败时不得加载组件'))
    paths = [c[0] for c in calls]
    assert paths[0] == '/status' and calls[0][1]['state'] == 'error' and '恢复失败' in calls[0][1]['error']
    desktop = next(data for path, data, _ in calls if path == '/result/d')
    assert desktop['result']['state'] == 'recovery_failed'
    assert run.call_count == 1 and run.call_args.args[2]['action'] == 'system_info'
    uninstall.assert_called_once_with(tmp_path)
    assert all(h.get('recovery') == 'recovery_failed' for _, _, h in calls)
    assert '/assets/worker.py' not in paths
    assert '恢复失败' in (tmp_path / 'state.txt').read_text(encoding='utf-8')


def test_health_codes_are_sent_as_ascii_header(tmp_path):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{}'

    def urlopen(req, timeout):
        seen.update(req.headers)
        return Response()
    agent.HEALTH['recovery'] = 'recovery_failed'
    with patch.object(agent.urllib.request, 'urlopen', side_effect=urlopen):
        agent.request({'server': 'https://x', 'id': 'n', 'token': 't'}, '/poll')
    assert seen['X-agent-health'] == 'recovery_failed'


# ---------------- 7. 任务总时限 ----------------

def test_job_deadline_uses_job_timeout_plus_margin():
    assert agent.job_deadline({'request': {}}) == agent.JOB_DEFAULT_DEADLINE
    assert agent.job_deadline({'request': {'timeout': 900}}) == 900 + agent.JOB_DEADLINE_MARGIN
    assert agent.job_deadline({'request': {'timeout': 10 ** 6}}) == agent.JOB_DEADLINE_MAX
    assert agent.job_deadline({'request': {'timeout': True}}) == agent.JOB_DEFAULT_DEADLINE


def test_stuck_job_stops_online_heartbeat_reports_failure_and_restarts(tmp_path):
    release = threading.Event()
    calls = []

    def request(config, path, data=None):
        calls.append((path, data))

    def stuck(*args):
        assert release.wait(5), '看门狗未触发'
        return {'result': {'ok': True}}

    with patch.object(agent, 'JOB_HEARTBEAT_INTERVAL', 0.01), \
         patch.object(agent, 'job_deadline', return_value=0.2), \
         patch.object(agent, 'request', side_effect=request), \
         patch.object(agent, 'run_job', side_effect=stuck), \
         patch.object(agent, 'kill_job_processes') as kill, \
         patch.object(agent, 'schedule_restart') as restart, \
         patch.object(agent.os, '_exit', side_effect=lambda code: release.set()) as exit_:
        with pytest.raises(agent.JobStuck):
            agent.run_with_heartbeat(CONFIG, tmp_path, {'id': 'j1', 'action': 'click', 'request': {}}, {})
    result = next(i for i, (path, _) in enumerate(calls) if path == '/result/j1')
    assert calls[result][1]['result']['state'] == 'job_timeout' and calls[result][1]['result']['ok'] is False
    assert all(data == {'state': 'online'} for path, data in calls[:result])
    assert all(data != {'state': 'online'} for path, data in calls[result:])
    assert any(path == '/status' and data['state'] == 'error' for path, data in calls[result:])
    kill.assert_called_once()
    restart.assert_called_once_with(CONFIG, tmp_path)
    exit_.assert_called_once()
    assert agent.HEALTH.get('job') == 'job_stuck'


def test_main_exits_after_watchdog_fired(tmp_path):
    job = {'job': {'id': 'j', 'action': 'click', 'request': {}}}
    with patch.object(agent, 'run_with_heartbeat', side_effect=agent.JobStuck('j')):
        calls = _main(tmp_path, [job, {'job': None}], startup_assets=lambda c, r: None)
    assert [c[0] for c in calls] == ['/poll']        # 卡死后直接退出, 不再回传结果或继续轮询
