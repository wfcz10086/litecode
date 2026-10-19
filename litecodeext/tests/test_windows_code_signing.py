"""Windows 节点代码下发完整性: 安装命令锚点 + 签名清单 + 旧节点首次信任。

Windows 端在 Linux 不能真跑: 代理的网络请求用 mock 回放; PowerShell 部分做结构断言 (校验先于执行/写入)。
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.error
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib import windows_node_agent as agent
from lib import windows_node_signing as signing
from lib.windows_nodes import Nodes, WORKER_FILES, component_versions
from tests.test_windows_nodes import enroll

pytestmark = pytest.mark.skipif(not signing.available(), reason='控制端缺少 cryptography')


@pytest.fixture(scope='module')
def workspace(tmp_path_factory):
    path = tmp_path_factory.mktemp('signing-ws')
    signing.public_key_info(path)        # 生成一次密钥, 本模块共用 (RSA-3072 生成较慢)
    return path


def _served_bytes():
    """控制端实际下发的字节, 按代理端资产名。"""
    return {asset: (ROOT / 'lib' / name).read_text().encode() for asset, name in zip(agent.ASSETS, WORKER_FILES)}


def _pin(root, workspace, pinned_by='install', last_issued=0):
    info = signing.public_key_info(workspace)
    trust = {'key': info['key'], 'fingerprint': info['fingerprint'], 'pinned_by': pinned_by, 'last_issued': last_issued}
    (root / agent.TRUST_FILE).write_text(json.dumps(trust), encoding='utf-8')
    return trust


def _sign(workspace, manifest):
    """用控制端私钥给任意清单签名 (构造新版本/篡改场景)。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    message = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    signature = signing._private_key(workspace).sign(message, padding.PKCS1v15(), hashes.SHA256())
    return {'manifest': base64.b64encode(message).decode(), 'signature': base64.b64encode(signature).decode()}


def _manifest_for(files_by_asset, agent_bytes=b'', issued=1000.0):
    combined = hashlib.sha256()
    for asset in agent.ASSETS:
        combined.update(files_by_asset[asset])
    files = {agent._served(a): hashlib.sha256(d).hexdigest() for a, d in files_by_asset.items()}
    files[agent.AGENT_FILE] = hashlib.sha256(agent_bytes).hexdigest()
    return {'format': signing.MANIFEST_FORMAT, 'issued': issued, 'files': files,
            'versions': {'assets': combined.hexdigest()[:16], 'agent': hashlib.sha256(agent_bytes).hexdigest()[:16]}}


def _write_old(root):
    for asset in agent.ASSETS:
        (root / asset).write_bytes(b'# old ' + asset.encode())


def _assert_old(root):
    assert all((root / a).read_bytes() == b'# old ' + a.encode() for a in agent.ASSETS)
    assert not (root / '.update').exists()


def _serve(contents, versions_payload=None, key_info=None):
    def response(config, path, data=None):
        if path == '/versions':
            if versions_payload is None:
                raise urllib.error.HTTPError('', 404, 'none', {}, None)
            return json.dumps(versions_payload).encode()
        if path == '/signing-key':
            if key_info is None:
                raise urllib.error.HTTPError('', 404, 'none', {}, None)
            return json.dumps(key_info).encode()
        return contents[path.rsplit('/', 1)[-1]]
    return response


# ---------------- 签名算法: 纯标准库验签与 cryptography 签名互通 ----------------

def test_stdlib_rsa_verify_interoperates_and_rejects_tampering(workspace):
    key = signing.public_key(workspace)
    assert agent.valid_key(key) and agent.key_fingerprint(key) == signing.fingerprint(key)
    signed = signing.signed_manifest(workspace)
    message, signature = base64.b64decode(signed['manifest']), base64.b64decode(signed['signature'])
    assert agent.rsa_verify(key, message, signature)
    assert not agent.rsa_verify(key, message + b' ', signature)                       # 清单改一个字节
    flipped = bytearray(signature); flipped[-1] ^= 1
    assert not agent.rsa_verify(key, message, bytes(flipped))                          # 签名改一位
    assert not agent.rsa_verify(key, message, signature[1:])                           # 长度不对
    from cryptography.hazmat.primitives.asymmetric import rsa
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_numbers()
    other_key = {'alg': agent.KEY_ALG, 'n': base64.b64encode(signing._int_bytes(other.n)).decode(),
                 'e': base64.b64encode(signing._int_bytes(other.e)).decode()}
    assert not agent.rsa_verify(other_key, message, signature)                         # 别的公钥
    weak = dict(key, n=base64.b64encode((12345).to_bytes(2, 'big')).decode())
    assert not agent.valid_key(weak) and not agent.rsa_verify(weak, message, signature)


def test_private_key_stays_on_server(workspace, tmp_path):
    from lib.key_store import windows_key_dir
    path = windows_key_dir(workspace) / 'signing_key.pem'
    assert path.stat().st_mode & 0o777 == 0o600
    assert not (workspace / '.secrets/windows_nodes/signing_key.pem').exists()   # 私钥不在工作区
    nodes = Nodes(workspace)
    _, _, device = enroll(nodes, 'keyonly')
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    app = FastAPI(); app.include_router(router.router)
    client = TestClient(app)
    headers = {'Authorization': 'Bearer ' + device['token']}
    prefix = '/api/windows/device/' + device['id']
    with patch.object(router, 'manager', return_value=nodes):
        public = client.get('/api/windows/signing-key')
        device_key = client.get(prefix + '/signing-key', headers=headers)
        denied = client.get(prefix + '/signing-key', headers={'Authorization': 'Bearer wrong'})
        poll = client.get(prefix + '/poll', headers=headers).json()
        versions = client.get(prefix + '/versions', headers=headers).json()
    assert public.status_code == 200 and public.json() == device_key.json() == signing.public_key_info(workspace)
    assert denied.status_code == 403
    for text in (public.text, device_key.text, json.dumps(poll), json.dumps(versions)):
        assert 'PRIVATE' not in text and set(json.loads(text)) & {'d', 'p', 'q'} == set()
    trust = {'key': public.json()['key'], 'last_issued': 0}
    for payload in (poll, versions):
        manifest = agent.verified_manifest(trust, payload['signed_manifest'])
        assert manifest['versions'] == component_versions()
        assert set(manifest['files']) == {'windows_node_agent.py', *WORKER_FILES}


# ---------------- 正确签名的清单被接受 ----------------

def test_signed_manifest_accepted_and_issued_recorded(workspace, tmp_path):
    trust = _pin(tmp_path, workspace)
    served = _served_bytes()
    payload = {'versions': component_versions(), 'signed_manifest': signing.signed_manifest(workspace)}
    with patch.object(agent, 'request', side_effect=_serve(served, payload)):
        got = agent.startup_assets({}, tmp_path)
    assert got['fingerprint'] == trust['fingerprint']
    assert agent.VERSIONS['assets'] == component_versions()['assets'] == agent.assets_version(tmp_path)
    assert (tmp_path / 'windows_desktop_worker.py').read_bytes() == served['worker.py']
    issued = json.loads(base64.b64decode(payload['signed_manifest']['manifest']))['issued']
    assert json.loads((tmp_path / agent.TRUST_FILE).read_text())['last_issued'] == issued


# ---------------- 篡改任一文件或清单: 拒绝且一个文件都不替换 ----------------

@pytest.mark.parametrize('victim', agent.ASSETS)
def test_tampered_file_rejected_nothing_replaced(workspace, tmp_path, victim):
    _pin(tmp_path, workspace)
    _write_old(tmp_path)
    new = {a: b'# new ' + a.encode() for a in agent.ASSETS}
    signed = _sign(workspace, _manifest_for(new))
    tampered = dict(new, **{victim: b'# evil ' + victim.encode()})
    with patch.object(agent, 'request', side_effect=_serve(tampered, {'signed_manifest': signed})):
        agent.startup_assets({}, tmp_path)            # 本地已有完整旧组件: 继续用旧的
    _assert_old(tmp_path)
    assert '拒绝' in (tmp_path / 'state.txt').read_text(encoding='utf-8')


def test_tampered_or_stripped_manifest_rejected(workspace, tmp_path):
    trust = _pin(tmp_path, workspace)
    _write_old(tmp_path)
    evil = {a: b'# evil ' + a.encode() for a in agent.ASSETS}
    good = _manifest_for({a: b'# new ' + a.encode() for a in agent.ASSETS})
    signed = _sign(workspace, good)
    # 1) 改清单里的哈希 (签名不变)
    forged = dict(good, files=_manifest_for(evil)['files'], versions=_manifest_for(evil)['versions'])
    forged_payload = {'manifest': base64.b64encode(json.dumps(forged).encode()).decode(), 'signature': signed['signature']}
    # 2) 用别的私钥签名
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    message = json.dumps(_manifest_for(evil)).encode()
    other_signed = {'manifest': base64.b64encode(message).decode(), 'signature': base64.b64encode(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()}
    # 3) 去掉签名清单, 只给与恶意文件吻合的未签名 versions/asset_hashes (旧字段)
    unsigned = {'versions': _manifest_for(evil)['versions'], 'asset_hashes': _manifest_for(evil)['files']}
    for payload in ({'signed_manifest': forged_payload}, {'signed_manifest': other_signed}, unsigned,
                    {'signed_manifest': {'manifest': 'not base64!', 'signature': ''}}):
        with patch.object(agent, 'request', side_effect=_serve(evil, payload)):
            agent.startup_assets({}, tmp_path)
        _assert_old(tmp_path)
        with pytest.raises(ValueError):
            agent.verified_manifest(trust, payload.get('signed_manifest'))


def test_replayed_older_manifest_rejected(workspace, tmp_path):
    trust = _pin(tmp_path, workspace, last_issued=2000.0)
    _write_old(tmp_path)
    old_release = {a: b'# release-1 ' + a.encode() for a in agent.ASSETS}
    signed = _sign(workspace, _manifest_for(old_release, issued=1000.0))    # 真签名, 但早于已接受清单
    with pytest.raises(ValueError, match='回放'):
        agent.verified_manifest(trust, signed)
    with patch.object(agent, 'request', side_effect=_serve(old_release, {'signed_manifest': signed})):
        agent.startup_assets({}, tmp_path)
    _assert_old(tmp_path)


def test_corrupt_trust_file_never_downgrades_to_unsigned(tmp_path):
    (tmp_path / agent.TRUST_FILE).write_text('{"key": {"alg": "RSA-PKCS1v15-SHA256", "n": "AA==", "e": "AQAB"}}')
    _write_old(tmp_path)
    evil = {a: b'# evil ' + a.encode() for a in agent.ASSETS}
    unsigned = {'versions': _manifest_for(evil)['versions'], 'asset_hashes': _manifest_for(evil)['files']}
    with patch.object(agent, 'request', side_effect=_serve(evil, unsigned, key_info=None)):
        trust = agent.startup_assets({}, tmp_path)
    assert trust == {'broken': True}
    _assert_old(tmp_path)
    assert agent.update_target(trust, unsigned) == ({}, None)


# ---------------- 代理自更新: 已固定公钥时只认验签清单的完整哈希 ----------------

def _run_main(tmp_path, polls, served):
    calls = []
    def response(config, path, data=None):
        calls.append(path)
        if path == '/poll':
            if not polls:
                raise KeyboardInterrupt
            return json.dumps(polls.pop(0)).encode()
        return served(path)
    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value={'id': 'a' * 32}), \
         patch.object(agent, 'request', side_effect=response), \
         patch.object(agent, 'startup_assets', side_effect=lambda c, r: agent.load_trust(r)), \
         patch.object(agent.time, 'sleep'), patch.object(agent.os, 'chdir'), \
         patch.object(agent, 'harden_task'), \
         patch.object(agent, 'powershell') as popen:
        popen.return_value.returncode = 0
        try:
            agent.main()          # 自更新成功会安排重启并返回
        except KeyboardInterrupt:
            pass                  # 轮询回放完毕
    return calls, popen


def test_pinned_agent_update_requires_signature(workspace, tmp_path):
    _pin(tmp_path, workspace)
    (tmp_path / 'agent.py').write_bytes(b'# old agent')
    evil, good = b'# evil agent', b'# new agent'
    files = _served_bytes()
    served = lambda bad: (lambda path: bad if path == '/assets/agent.py' else files[path.rsplit('/', 1)[-1]])
    # 未签名的 versions 指向恶意代理 (旧字段, 哈希与恶意内容吻合): 已固定公钥的节点不理会
    unsigned = {'job': None, 'versions': {'agent': hashlib.sha256(evil).hexdigest()[:16]}}
    calls, popen = _run_main(tmp_path, [unsigned], served(evil))
    assert '/assets/agent.py' not in calls and not popen.called
    # 签名清单是新代理, 但途中下载内容被替换: 不替换不重启
    signed = _sign(workspace, _manifest_for(files, agent_bytes=good, issued=5000.0))
    calls, popen = _run_main(tmp_path, [{'job': None, 'signed_manifest': signed}], served(evil))
    assert (tmp_path / 'agent.py').read_bytes() == b'# old agent' and not popen.called
    # 签名清单 + 内容一致: 原子替换并重启, 记下签发时间
    calls, popen = _run_main(tmp_path, [{'job': None, 'signed_manifest': signed}], served(good))
    assert (tmp_path / 'agent.py').read_bytes() == good and popen.called
    assert json.loads((tmp_path / agent.TRUST_FILE).read_text())['last_issued'] == 5000.0


# ---------------- 旧节点: 首次信任固定公钥, 之后要求验签 ----------------

def test_legacy_node_pins_key_on_first_use_then_requires_signature(workspace, tmp_path):
    assert not (tmp_path / agent.TRUST_FILE).exists()
    served = _served_bytes()
    payload = {'versions': component_versions(), 'signed_manifest': signing.signed_manifest(workspace)}
    info = signing.public_key_info(workspace)
    with patch.object(agent, 'request', side_effect=_serve(served, payload, key_info=info)):
        trust = agent.startup_assets({}, tmp_path)
    saved = json.loads((tmp_path / agent.TRUST_FILE).read_text())
    assert saved['pinned_by'] == 'first_use' and saved['fingerprint'] == info['fingerprint'] == trust['fingerprint']
    assert agent.assets_version(tmp_path) == component_versions()['assets']
    # 固定之后: 去掉签名、只给未签名哈希的更新一律拒绝
    _write_old(tmp_path)
    evil = {a: b'# evil ' + a.encode() for a in agent.ASSETS}
    unsigned = {'versions': _manifest_for(evil)['versions'], 'asset_hashes': _manifest_for(evil)['files']}
    with patch.object(agent, 'request', side_effect=_serve(evil, unsigned, key_info=info)):
        agent.startup_assets({}, tmp_path)
    _assert_old(tmp_path)
    assert agent.update_target(agent.load_trust(tmp_path), unsigned) == ({}, None)
    # 途中换公钥也无效: 已固定的公钥不会被再次覆盖
    from cryptography.hazmat.primitives.asymmetric import rsa
    numbers = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_numbers()
    rogue = {'alg': agent.KEY_ALG, 'n': base64.b64encode(signing._int_bytes(numbers.n)).decode(),
             'e': base64.b64encode(signing._int_bytes(numbers.e)).decode()}
    with patch.object(agent, 'request', side_effect=_serve(evil, unsigned, key_info={'key': rogue})):
        agent.startup_assets({}, tmp_path)
    assert json.loads((tmp_path / agent.TRUST_FILE).read_text())['fingerprint'] == info['fingerprint']


def test_server_without_signing_keeps_legacy_hash_check(tmp_path):
    served = _served_bytes()
    from lib.windows_nodes import asset_hashes
    payload = {'versions': component_versions(), 'asset_hashes': asset_hashes()}
    with patch.object(agent, 'request', side_effect=_serve(served, payload, key_info=None)):
        assert agent.startup_assets({}, tmp_path) is None
    assert not (tmp_path / agent.TRUST_FILE).exists()
    assert agent.assets_version(tmp_path) == component_versions()['assets']


# ---------------- 安装命令携带锚点; 锚点不符时 bootstrap 不执行 ----------------

def _script(command):
    return base64.b64decode(command.split()[-1]).decode('utf-16le')


def test_install_command_anchors_bootstrap_and_key(workspace):
    nodes = Nodes(workspace)
    script = _script(nodes.create('anchor', 'http://192.168.1.10:18790')['command'])
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    app = FastAPI(); app.include_router(router.router)
    with patch.object(router, 'manager', return_value=nodes):
        client = TestClient(app)
        served = client.get('/api/windows/bootstrap.ps1').content
        key = client.get('/api/windows/signing-key').json()
    anchor = re.search(r"\$h='([0-9a-f]{64})'", script)[1]
    assert anchor == hashlib.sha256(served).hexdigest() == signing.bootstrap_sha256()
    assert re.search(r"-KeyFingerprint '([0-9a-f]{64})'", script)[1] == key['fingerprint'] \
        == agent.key_fingerprint(key['key'])
    # 先下载字节 → 核对 sha256 → 不符 throw → 只有通过后才由同一份字节构造并执行脚本块
    download, check, decode, run = (script.index(s) for s in (
        "$raw=LcGetBytes ($s+'/api/windows/bootstrap.ps1')", 'if((LcSha256 $raw) -ne $h){throw', '$b=[Text.Encoding]::UTF8.GetString($raw)',
        '& ([scriptblock]::Create($b))'))
    assert download < check < decode < run
    assert '安装脚本校验失败' in script and 'Invoke-RestMethod' not in script
    # 途中被替换的引导脚本 (哪怕只多一个字节) 与锚点不符 → 上面的 throw 拦下, 不会执行
    assert hashlib.sha256(served + b'\n').hexdigest() != anchor


def test_bootstrap_verifies_key_before_enroll_and_files_before_write():
    source = (ROOT / 'lib/windows_node_bootstrap.ps1').read_text()
    assert "[string]$KeyFingerprint = ''" in source            # 旧安装命令 (无指纹) 仍可运行
    key_check, enroll_at = source.index('签名公钥校验失败'), source.index('/api/windows/enroll')
    assert key_check < enroll_at                                  # 公钥不符时接入码都不消费
    verify, write_agent, pin = (source.index(s) for s in (
        'Assert-SignedFiles (Invoke-RestMethod "$device/versions"', "WriteAllBytes((Join-Path $root 'agent.py')",
        "pinned_by='install'"))
    assert verify < write_agent < pin
    assert "VerifyData($manifestBytes, 'SHA256'" in source and 'litecode-windows-manifest-v1' in source


def test_update_and_uninstall_commands_carry_hashes(workspace):
    nodes = Nodes(workspace)
    _, _, device = enroll(nodes, 'cmds')
    nodes.heartbeat(device['id'], device['token'], 'online')
    update = _script(nodes.update_command(device['id'])['command'])
    assert signing.agent_sha256() in update and signing.public_key_info(workspace)['fingerprint'] in update
    assert update.index('代理校验失败') < update.index('Move-Item')
    assert update.index('签名公钥校验失败') < update.index("pinned_by='update_command'")
    uninstall = _script(nodes.uninstall_command(device['id'])['command'])
    assert signing.uninstall_sha256() in uninstall
    assert uninstall.index('卸载脚本校验失败') < uninstall.index('& ([scriptblock]::Create($b)) -NodeId')


def test_manifest_cache_resigns_when_content_changes(workspace, monkeypatch):
    first = signing.signed_manifest(workspace)
    assert signing.signed_manifest(workspace) == first           # 内容不变: 同一签名包 (同一 issued)
    monkeypatch.setattr(signing, 'manifest_files', lambda: {'windows_node_agent.py': '0' * 64})
    second = signing.signed_manifest(workspace)
    issued = lambda s: json.loads(base64.b64decode(s['manifest']))['issued']
    assert second != first and issued(second) >= issued(first)
