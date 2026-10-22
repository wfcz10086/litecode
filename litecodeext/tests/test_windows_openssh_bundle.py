"""自带 OpenSSH (2026-10-20 真机: Add-WindowsCapability 0x80240007 → 整个接入失败)。

- 可选功能装不上 → 改用自带的微软官方 Win32-OpenSSH 包, 装上就照常配置 SSH
- 两条路都不行 → 跳过 SSH 继续安装 (节点靠桌面代理回连照样能用)
- 控制端下发安装包需要设备凭据; bootstrap 里写死的 sha256 与随仓库带的包一致
"""
import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'core'), str(Path(__file__).resolve().parent)]

from test_windows_ssh_bootstrap import enroll, run_ps, ssh_block, ssh_prelude, Nodes  # noqa: E402

BOOT = (ROOT / 'lib/windows_node_bootstrap.ps1').read_text(encoding='utf-8-sig')
ZIP = ROOT / 'lib/windows_vendor/OpenSSH-Win64-v10.0.0.0p2.zip'


def test_pinned_hash_matches_bundled_zip():
    pinned = re.search(r"\$OpenSshZipSha256 = '([0-9a-f]{64})'", BOOT).group(1)
    assert hashlib.sha256(ZIP.read_bytes()).hexdigest() == pinned
    assert 'Get-AuthenticodeSignature' in BOOT and 'O=Microsoft Corporation' in BOOT


def test_undo_function_identical_and_handles_portable():
    un = (ROOT / 'lib/windows_node_uninstall.ps1').read_text(encoding='utf-8-sig')
    cut = lambda s: s[s.index('# >>> LiteCode SSH 撤销'):s.index('# <<< LiteCode SSH 撤销')]  # noqa: E731
    assert cut(BOOT) == cut(un)
    assert '$Owner.portable' in cut(BOOT) and 'uninstall-sshd.ps1' in cut(BOOT)


def test_vendor_route_requires_device_credential(tmp_path):
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    app = FastAPI()
    app.include_router(router.router)
    with patch.object(router, 'manager', return_value=nodes):
        c = TestClient(app)
        url = '/api/windows/device/' + device['id'] + '/vendor/openssh.zip'
        assert c.get(url).status_code in (401, 403)
        r = c.get(url, headers={'Authorization': 'Bearer ' + device['token']})
        assert r.status_code == 200 and hashlib.sha256(r.content).digest() == hashlib.sha256(ZIP.read_bytes()).digest()
        assert c.get(url.replace('openssh.zip', '..%2Fwindows_nodes.py'),
                     headers={'Authorization': 'Bearer ' + device['token']}).status_code == 404


HARNESS = """
$ErrorActionPreference='Stop'; $script:installed=$false; $script:portable=0; $script:skipped=0; $script:warn=''
$enrollment=@{id=('a'*32)}; $SshPublicKey='ssh-ed25519 AAAA litecode-node-'+$enrollment.id
function Write-Host {}
function Write-Warning {param($m) $script:warn += $m}
function Report-Stage {param($s) if($s -eq 'ssh_skipped'){$script:skipped++}}
function Get-WindowsCapability {param([switch]$Online,$Name,$ErrorAction) @{Name='OpenSSH.Server~~~~0.0.1.0';State='NotPresent'}}
function Add-WindowsCapability {param([switch]$Online,$Name,$ErrorAction) throw 'Add-WindowsCapability 失败。错误代码 = 0x80240007'}
function Get-NetFirewallRule {param($Name,$ErrorAction) $null}
function Disable-NetFirewallRule {}
function New-NetFirewallRule {param($Name,$DisplayName,$Direction,$Protocol,$LocalPort,$Action,$RemoteAddress) $script:scope=$RemoteAddress}
function Set-Service {param($Name,$StartupType) $script:startType=$StartupType}
function Start-Service {param($Name) $script:started=$true}
function Get-Service {param($Name,$ErrorAction) if($script:installed){@{Status='Running';StartType='Automatic'}}}
function Restart-Service {param($Name) }
function icacls.exe {$global:LASTEXITCODE=0}
"""


def _run(tmp_path, portable_ok):
    program = tmp_path / 'Program Data'
    (program / 'ssh').mkdir(parents=True)
    (program / 'ssh' / 'sshd_config').write_text('#PasswordAuthentication yes\n')
    fake = ("function Install-LiteCodeOpenSsh { $script:portable++; " +
            ("$sshRun.portable='C:\\\\Program Files\\\\OpenSSH'; Save-SshOwner; $script:installed=$true }" if portable_ok
             else "throw '自带 OpenSSH 安装包下载失败或校验不符' }") + "\n")
    prefix = f"$env:ProgramData='{str(program).replace(chr(39), chr(39)*2)}'\n"
    script = (prefix + HARNESS + ssh_prelude(tmp_path) + fake + ssh_block() +
              "@{portable=$script:portable;skipped=$script:skipped;started=$script:started;warn=$script:warn} | ConvertTo-Json -Compress")
    return run_ps(tmp_path, script), program


def test_capability_failure_falls_back_to_bundled_openssh(tmp_path):
    result, program = _run(tmp_path, portable_ok=True)
    assert result['portable'] == 1 and result['skipped'] == 0 and result['started'] is True
    assert '0x80240007' in result['warn']
    keys = (program / 'ssh' / 'administrators_authorized_keys').read_text()
    assert 'litecode-node-' + 'a' * 32 in keys                      # 照常装公钥
    owner = (tmp_path / 'ssh-owner.json').read_text(encoding='utf-8-sig')
    assert 'OpenSSH' in owner and '"capability":""' in owner.replace(' ', '')   # 卸载按 portable 清理


def test_both_paths_fail_skips_ssh_without_aborting(tmp_path):
    result, program = _run(tmp_path, portable_ok=False)
    assert result['portable'] == 1 and result['skipped'] == 1     # 跳过但脚本没抛错 (run_ps 能拿到结果)
    assert not (program / 'ssh' / 'administrators_authorized_keys').exists() or \
        'litecode-node-' not in (program / 'ssh' / 'administrators_authorized_keys').read_text()
