"""一键安装 SSH: 每节点密钥、受限防火墙、旧环境保留及撤销。"""
import base64
import json
from pathlib import Path
import re
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.windows_nodes import Nodes
from lib import windows_node_agent as agent
from tests.test_windows_nodes import enroll
from tests.test_windows_node_lifecycle import run_ps


def test_install_command_has_unique_public_key_and_no_private_key(tmp_path):
    nodes = Nodes(tmp_path)
    invitations = [nodes.create(name, 'https://example.test') for name in ('one', 'two')]
    public_keys = []
    for invitation in invitations:
        script = base64.b64decode(invitation['command'].split()[-1]).decode('utf-16le')
        public = re.search(r"-SshPublicKey '([^']+)'", script)[1]
        assert public.endswith('litecode-node-' + invitation['id'])
        private = nodes.root / 'ssh' / invitation['id']
        derived = subprocess.run(['ssh-keygen', '-y', '-f', str(private)], check=True, capture_output=True, text=True).stdout.strip()
        assert derived.split()[:2] == public.split()[:2]
        assert private.stat().st_mode & 0o777 == 0o600
        assert 'PRIVATE KEY' not in script
        public_keys.append(public.split()[1])
    assert len(set(public_keys)) == 2


def test_revoke_erases_managed_private_key(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    private = nodes.root / 'ssh' / device['id']
    assert private.is_file()
    nodes.revoke(device['id'])
    assert not private.exists() and not private.with_suffix('.pub').exists()


def test_bootstrap_can_report_every_install_stage_to_controller(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    app = FastAPI()
    app.include_router(router.router)
    stages = re.findall(r"Report-Stage '([^']+)'", (ROOT / 'lib/windows_node_bootstrap.ps1').read_text())
    with patch.object(router, 'manager', return_value=nodes):
        client = TestClient(app)
        for stage in stages:
            response = client.post('/api/windows/device/' + device['id'] + '/status',
                                   headers={'Authorization': 'Bearer ' + device['token']},
                                   json={'state': 'installing', 'stage': stage})
            assert response.status_code == 200, (stage, response.text)
    assert 'ssh' in stages


@pytest.mark.parametrize('existing', ['new', 'capability', 'standalone'])
def test_ssh_install_starts_automatically_and_preserves_existing_environment(tmp_path, existing):
    program = tmp_path / 'Program Data'
    (program / 'ssh').mkdir(parents=True)
    config = program / 'ssh' / 'sshd_config'
    original = '#PasswordAuthentication yes\nMatch Group administrators\n  AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys\n'
    config.write_text(original)
    keys = program / 'ssh' / 'administrators_authorized_keys'
    keys.write_text('ssh-ed25519 AAAA existing-user\n')
    block = ssh_prelude(tmp_path) + ssh_block()
    prefix = (f"$env:ProgramData='{str(program).replace(chr(39), chr(39)*2)}'; "
              f"$script:installed=${str(existing != 'new').lower()}; $script:capability=${str(existing == 'capability').lower()};\n")
    harness = """
$ErrorActionPreference='Stop'; $script:added=0; $script:restarted=0; $script:scope=''; $script:defaultDisabled=$false
$enrollment=@{id=('a'*32)}; $SshPublicKey='ssh-ed25519 AAAA litecode-node-'+$enrollment.id
function Write-Host {}
function Get-WindowsCapability {param([switch]$Online,$Name) @{Name='OpenSSH.Server~~~~0.0.1.0';State=$(if($script:capability){'Installed'}else{'NotPresent'})}}
function Add-WindowsCapability {param([switch]$Online,$Name) $script:installed=$true; $script:capability=$true; $script:added++}
function Get-NetFirewallRule {param($Name) if($Name -eq 'OpenSSH-Server-In-TCP' -and $script:installed){@{Name=$Name;Enabled='True'}}}
function Disable-NetFirewallRule {$script:defaultDisabled=$true}
function New-NetFirewallRule {param($Name,$DisplayName,$Direction,$Protocol,$LocalPort,$Action,$RemoteAddress) $script:scope=$RemoteAddress}
function Set-Service {param($Name,$StartupType) $script:startType=$StartupType}
function Start-Service {param($Name) $script:started=$true}
function Get-Service {param($Name) if($script:installed){@{Status='Running';StartType='Automatic'}}}
function Restart-Service {param($Name) $script:restarted++}
function icacls.exe {$global:LASTEXITCODE=0}
"""
    suffix = "@{added=$script:added;restarted=$script:restarted;scope=$script:scope;disabled=$script:defaultDisabled;startType=$script:startType;started=$script:started} | ConvertTo-Json -Compress"
    result = run_ps(tmp_path, prefix + harness + block + block + suffix)
    assert result['startType'] == 'Automatic' and result['started'] is True
    assert keys.read_text().count('litecode-node-' + 'a' * 32) == 1
    assert 'existing-user' in keys.read_text()
    if existing != 'new':
        assert result['added'] == result['restarted'] == 0
        assert result['disabled'] is False and config.read_text() == original
    else:
        assert result['added'] == result['restarted'] == 1
        assert result['scope'] == 'LocalSubnet' and result['disabled'] is True
        assert config.read_text().startswith('PasswordAuthentication no\n')


def ssh_prelude(root):
    """bootstrap 里 SSH 步骤用到的撤销函数与登记函数 ($root = 节点目录)。"""
    source = (ROOT / 'lib/windows_node_bootstrap.ps1').read_text()
    prelude = source[source.index('# >>> LiteCode SSH 撤销'):source.index("try {\n    Report-Stage 'ssh'")]
    return "$root='" + str(root).replace("'", "''") + "'\n" + prelude


def ssh_block():
    source = (ROOT / 'lib/windows_node_bootstrap.ps1').read_text()
    return source.split("    Report-Stage 'ssh'\n", 1)[1].split("    Report-Stage 'python'\n", 1)[0]


def test_device_revocation_removes_only_its_ssh_keys(tmp_path):
    program, profile = tmp_path / 'program', tmp_path / 'profile'
    files = [program / 'ssh' / 'administrators_authorized_keys', profile / '.ssh' / 'authorized_keys']
    node_id = 'a' * 32
    for path in files:
        path.parent.mkdir(parents=True)
        path.write_text('ssh-ed25519 AAAA keep-me\nssh-ed25519 BBBB litecode-node-' + node_id + '\n')
    with patch.object(agent, 'powershell') as shell:
        agent.disable_task({'id': node_id})
    script = shell.call_args.args[0]
    prefix = f"$env:ProgramData='{program}'; $env:USERPROFILE='{profile}'; "
    harness = "function Disable-ScheduledTask {param($TaskName) $script:disabled=$true}; "
    result = run_ps(tmp_path, prefix + harness + script + '; @{disabled=$script:disabled} | ConvertTo-Json -Compress')
    assert result['disabled'] is True
    assert all(path.read_text().strip() == 'ssh-ed25519 AAAA keep-me' for path in files)


def test_fresh_installer_registers_one_minute_recovery_without_duplicate_agents(tmp_path):
    source = (ROOT / 'lib/windows_node_bootstrap.ps1').read_text()
    block = source.split("    $pythonw = Join-Path", 1)[1].split("    Write-Host ('已安装节点：'", 1)[0]
    block = '    $pythonw = Join-Path' + block
    root = tmp_path / "node user's folder"
    root.mkdir()
    harness = r"""
$ErrorActionPreference='Stop'
$identity=@{Name='LAB\installer'}; $enrollment=@{id=('a'*32)}
function New-ScheduledTaskAction {param($Execute,$Argument,$WorkingDirectory) @{execute=$Execute;args=$Argument;cwd=$WorkingDirectory}}
function New-ScheduledTaskPrincipal {param($UserId,$LogonType,$RunLevel) @{user=$UserId;logon=$LogonType}}
function New-ScheduledTaskTrigger {param([switch]$AtStartup,[switch]$AtLogOn,$User,[switch]$Once,$At,$RepetitionInterval) @{boot=[bool]$AtStartup;login=[bool]$AtLogOn;repeat=$RepetitionInterval.TotalMinutes}}
function New-ScheduledTaskSettingsSet {param($RestartCount,$RestartInterval,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,$MultipleInstances,[switch]$StartWhenAvailable) @{restarts=$RestartCount;retryMinutes=$RestartInterval.TotalMinutes;limit=$ExecutionTimeLimit.TotalSeconds;instances=$MultipleInstances}}
function Stop-ScheduledTask {param($TaskName) $script:stopped=$TaskName}
function Register-ScheduledTask {param($TaskName,$Action,$Trigger,$Principal,$Settings,[switch]$Force) $script:registered=@{task=$TaskName;action=$Action;triggers=$Trigger;principal=$Principal;settings=$Settings}}
function Start-ScheduledTask {param($TaskName) if($TaskName -ne $script:stopped -or $TaskName -ne $script:registered.task){throw 'existing task not stopped before reinstall'}; $script:registered | ConvertTo-Json -Depth 8 -Compress}
"""
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    result = run_ps(tmp_path, '$root=' + quote(root) + '; $python=' + quote(root / 'venv/Scripts/python.exe') + ';\n' + harness + block)
    assert result['action']['cwd'] == str(root)
    assert result['action']['execute'].endswith('pythonw.exe')
    assert str(root / 'agent.py') in result['action']['args']
    assert result['principal']['logon'] == 'Interactive'
    assert result['settings'] == {'restarts': 999, 'retryMinutes': 1, 'limit': 0, 'instances': 'IgnoreNew'}
    assert len(result['triggers']) == 3
    assert any(t['boot'] for t in result['triggers']) and any(t['login'] for t in result['triggers'])
    assert any(t['repeat'] == 1 for t in result['triggers'])
