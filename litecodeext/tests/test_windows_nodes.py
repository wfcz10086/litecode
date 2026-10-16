"""纯 Windows 出站节点的登记、隔离、截图回传及任务通道测试。"""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.windows_nodes import Nodes, server_url


def enroll(nodes, alias='test-node'):
    created = nodes.create(alias, 'https://litecode.example.com')
    script = base64.b64decode(created['command'].split()[-1]).decode('utf-16le')
    code = re.search(r"-Code '([^']+)'", script)[1]
    return created, code, nodes.redeem(code, {'computer': 'test', 'user': 'test'})


def complete_initial(nodes, device):
    task = nodes.claim(device['id'], device['token'])
    assert task['action'] == 'screenshot'
    raw = io.BytesIO()
    Image.new('RGB', (40, 30), 'blue').save(raw, 'PNG')
    nodes.finish(device['id'], device['token'], task['id'],
                 {'ok': True, 'state': 'desktop_ready', 'image': 'screen.png'},
                 {'screen.png': base64.b64encode(raw.getvalue()).decode()})


@pytest.mark.parametrize('url', ["https://good.com';bad", 'http://8.8.8.8',
    'https://user:pass@example.com', 'https://example.com/path', 'file:///tmp/test',
    'https://example.com/?code=secret', 'https://example.com:bad'])
def test_url_rejects_injection_and_public_cleartext(url):
    with pytest.raises(ValueError):
        server_url(url)


def test_one_use_code_and_independent_token(tmp_path):
    nodes = Nodes(tmp_path)
    created, code, device = enroll(nodes)
    assert '\n' not in created['command']
    disk = (nodes.root / 'nodes.json').read_text()
    assert code not in disk and device['token'] not in disk
    assert not {'code_hash', 'token_hash'} & nodes.list()[0].keys()
    with pytest.raises(ValueError):
        nodes.redeem(code, {})
    with pytest.raises(ValueError):
        nodes.authenticate(device['id'], 'wrong')
    with pytest.raises(ValueError):
        nodes.create('test-node', 'https://example.com')
    with pytest.raises(ValueError):
        nodes.create('win11', 'https://example.com', ['win11'])


def test_expired_code_cannot_enroll(tmp_path):
    nodes = Nodes(tmp_path)
    created = nodes.create('expired', 'http://192.168.1.2:18790')
    code = re.search(r"-Code '([^']+)'", base64.b64decode(created['command'].split()[-1]).decode('utf-16le'))[1]
    with nodes.locked() as data:
        data['expired']['expires'] = time.time() - 1
    with pytest.raises(ValueError):
        nodes.redeem(code, {})


def test_initial_screenshot_and_revocation(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    assert nodes.list()[0]['state'] == 'installing'
    complete_initial(nodes, device)
    node = nodes.list()[0]
    assert node['state'] == 'online' and node['desktop_state'] == 'desktop_ready'
    assert Path(node['screenshot']).is_relative_to(tmp_path / 'uploads')
    assert not list(nodes.root.glob('job-*.json'))
    nodes.revoke(device['id'])
    with pytest.raises(ValueError):
        nodes.claim(device['id'], device['token'])


def test_jobs_are_isolated_and_not_replayed(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, first = enroll(nodes, 'first')
    _, _, second = enroll(nodes, 'second')
    complete_initial(nodes, first)
    complete_initial(nodes, second)
    job = nodes.job('first', 'type_secret', {'text': 'private-value', 'sensitive': True})
    with pytest.raises(ValueError, match='已有桌面任务'):
        nodes.job('first', 'screenshot', {})
    claimed = nodes.claim(first['id'], first['token'])
    assert claimed['request']['text'] == 'private-value'
    assert 'private-value' not in (nodes.root / ('job-' + job + '.json')).read_text()
    assert nodes.claim(first['id'], first['token']) is None
    with pytest.raises(ValueError):
        nodes.finish(second['id'], second['token'], job, {'ok': True}, {})
    nodes.finish(first['id'], first['token'], job, {'ok': True}, {})
    assert nodes.wait(job, 1)['ok']
    with pytest.raises(ValueError):
        nodes.finish(first['id'], first['token'], job, {'ok': True}, {})


def test_desktop_uses_outbound_node_without_ssh(tmp_path):
    from lib.windows_desktop import execute
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    with patch('core.tools.handlers.remote._connect') as ssh, ThreadPoolExecutor() as pool:
        future = pool.submit(execute, {'alias': 'test-node', 'action': 'windows', 'timeout': 10}, tmp_path)
        deadline = time.monotonic() + 3
        task = None
        while not task and time.monotonic() < deadline:
            task = nodes.claim(device['id'], device['token'])
            if not task:
                time.sleep(0.01)
        assert task and task['action'] == 'windows'
        nodes.finish(device['id'], device['token'], task['id'], {'ok': True, 'windows': []}, {})
        assert future.result(timeout=3)['alias'] == 'test-node'
        ssh.assert_not_called()


def test_timeout_does_not_report_success(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    job = nodes.job('test-node', 'hotkey', {'keys': 'enter'})
    nodes.claim(device['id'], device['token'])
    with pytest.raises(TimeoutError, match='勿重复发送'):
        nodes.wait(job, 0.01)
    assert not (nodes.root / ('job-' + job + '.json')).exists()


def test_api_authentication_and_assets(tmp_path):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    nodes = Nodes(tmp_path)
    app = FastAPI()
    app.include_router(router.router)
    with patch.object(router, 'manager', return_value=nodes), patch('plugins.auth.core._AUTH_ENABLED', True):
        with patch.object(router, 'require_auth', side_effect=HTTPException(401, '请登录')):
            assert TestClient(app).get('/api/windows/nodes').status_code == 401
        _, _, device = enroll(nodes)
        client = TestClient(app)
        prefix = '/api/windows/device/' + device['id']
        assert client.get(prefix + '/assets/agent.py').status_code == 403
        headers = {'Authorization': 'Bearer ' + device['token']}
        assert client.get(prefix + '/assets/agent.py', headers=headers).status_code == 200
        assert client.get(prefix + '/assets/config.json', headers=headers).status_code == 404
        task = client.get(prefix + '/poll', headers=headers).json()['job']
        response = client.post(prefix + '/result/' + task['id'], headers=headers,
                               json={'result': {'ok': False, 'state': 'desktop_unavailable'}, 'images': {}})
        assert response.status_code == 200
        assert nodes.list()[0]['desktop_state'] == 'desktop_unavailable'


def test_complete_uninstall_requires_device_receipt(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    job_id = nodes.job('test-node', 'type_secret', {'text': 'private-value'})
    response = nodes.uninstall(device['id'])
    assert response['state'] == 'uninstall_pending'
    assert not (nodes.root / ('job-' + job_id + '.json')).exists()
    with pytest.raises(RuntimeError, match='取消'):
        nodes.wait(job_id, 1)
    # 等待卸载时仍保留回执认证，但不能再接受桌面任务。
    nodes.authenticate(device['id'], device['token'])
    with pytest.raises(ValueError):
        nodes.job('test-node', 'screenshot', {})
    job = nodes.claim(device['id'], device['token'])
    assert job['action'] == 'uninstall'
    assert nodes.list()[0]['state'] == 'uninstalling'
    nodes.heartbeat(device['id'], device['token'])
    assert nodes.list()[0]['state'] == 'uninstalling'
    nodes.uninstalled(device['id'], device['token'])
    assert nodes.list()[0]['state'] == 'uninstalled'
    disk = (nodes.root / 'nodes.json').read_text()
    assert 'token_hash' not in disk and 'code_hash' not in disk
    with pytest.raises(ValueError):
        nodes.claim(device['id'], device['token'])


def test_offline_uninstall_waits_and_failure_is_retryable(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    with nodes.locked() as data:
        data['test-node']['last_seen'] = time.time() - 100
    assert nodes.list()[0]['state'] == 'offline'
    nodes.uninstall(device['id'])
    assert nodes.list()[0]['state'] == 'uninstall_pending'
    nodes.claim(device['id'], device['token'])
    nodes.heartbeat(device['id'], device['token'], 'uninstall_failed', '文件仍被占用')
    assert nodes.list()[0]['state'] == 'uninstall_failed'
    assert nodes.claim(device['id'], device['token']) is None
    assert nodes.uninstall(device['id'])['state'] == 'uninstall_pending'


def test_uninstall_command_works_locally_without_token(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    command = nodes.uninstall_command(device['id'])['command']
    script = base64.b64decode(command.split()[-1]).decode('utf-16le')
    assert device['token'] not in command and device['token'] not in script
    assert "if(Test-Path $p){ & $p -Root" in script
    nodes.revoke(device['id'])
    with pytest.raises(ValueError, match='本机卸载'):
        nodes.uninstall(device['id'])
    assert nodes.uninstall_command(device['id'])


def test_task_receipt_is_distinct_from_verified_business_success(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    assert nodes.list()[0]['desktop_ready'] is True
    job_id = nodes.job('test-node', 'hotkey', {'keys': 'enter'})
    assert nodes.list()[0]['last_task']['state'] == 'queued'
    nodes.claim(device['id'], device['token'])
    assert nodes.list()[0]['last_task']['state'] == 'running'
    nodes.finish(device['id'], device['token'], job_id, {'ok': True}, {})
    assert nodes.list()[0]['last_task']['state'] == 'returned'
    assert 'keys' not in json.dumps(nodes.list())
    assert nodes.list()[0]['desktop_ready'] is False


def test_management_refuses_auth_disabled():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    app = FastAPI(); app.include_router(router.router)
    with patch('plugins.auth.core._AUTH_ENABLED', False):
        assert TestClient(app).get('/api/windows/nodes').status_code == 503


def test_expired_job_is_purged_after_controller_restart(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    job_id = nodes.job('test-node', 'type_secret', {'text': 'private-value'})
    file = nodes.root / ('job-' + job_id + '.json')
    job = json.loads(file.read_text()); job['expires'] = time.time() - 1
    file.write_text(json.dumps(job))
    assert Nodes(tmp_path).list()[0]['last_task']['state'] == 'unconfirmed'
    assert not file.exists()


def test_api_uninstall_callback_is_authenticated_and_not_cached(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    app = FastAPI(); app.include_router(router.router)
    with patch.object(router, 'manager', return_value=nodes), patch.object(router, 'require_management'):
        client = TestClient(app)
        r = client.post('/api/windows/nodes/' + device['id'] + '/uninstall')
        assert r.json()['state'] == 'uninstall_pending'
        assert r.headers['cache-control'] == 'no-store'
        path = '/api/windows/device/' + device['id'] + '/uninstalled'
        assert client.post(path).status_code == 403
        assert client.post(path, headers={'Authorization': 'Bearer ' + device['token']}).status_code == 200
        assert nodes.list()[0]['state'] == 'uninstalled'


def test_agent_cleans_screenshots_and_never_writes_request(tmp_path):
    from lib.windows_node_agent import run_job
    from types import SimpleNamespace
    def run(action, folder, request):
        Image.new('RGB', (4, 4)).save(folder / 'screen.png')
        return {'ok': True, 'image': 'screen.png'}
    job = {'id': 'abc', 'action': 'type_secret', 'request': {'text': 'secret'}}
    with patch.dict(sys.modules, {'windows_desktop_worker': SimpleNamespace(run=run)}):
        payload = run_job({}, tmp_path, job)
    assert payload['images']['screen.png']
    assert not list((tmp_path / 'jobs').iterdir())


def test_bootstrap_can_resume_without_ssh():
    source = (ROOT / 'lib/windows_node_bootstrap.ps1').read_text()
    assert 'Get-AuthenticodeSignature' in source
    assert 'Test-Path $configFile' in source
    assert '-LogonType Interactive -RunLevel Highest' in source
    assert 'New-NetFirewallRule' not in source and 'Add-WindowsCapability' not in source


def test_remove_only_finished_nodes_and_frees_alias(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes, 'old-pc')
    with pytest.raises(ValueError):
        nodes.remove(device['id'])          # 仍可连回的节点必须先撤销/卸载
    nodes.revoke(device['id'])
    assert nodes.remove(device['id']) is True
    assert nodes.list() == []
    enroll(nodes, 'old-pc')                  # 别名释放后可重新接入
    pending = nodes.create('fresh', 'https://litecode.example.com')
    with pytest.raises(ValueError):
        nodes.remove(pending['id'])          # 未过期的接入码不删, 防止误删正在安装的电脑
    with nodes.locked() as data:
        data['fresh']['expires'] = time.time() - 1
    assert nodes.remove(pending['id']) is True
    with pytest.raises(ValueError):
        nodes.remove('missing')


def test_version_report_drives_update_state(tmp_path):
    from lib.windows_nodes import component_versions
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    assert nodes.list()[0]['update_state'] == 'legacy'          # 旧代理不上报版本
    current = component_versions()
    nodes.claim(device['id'], device['token'], {'agent': '0' * 16, 'assets': current['assets']})
    assert nodes.list()[0]['update_state'] == 'updating'
    nodes.claim(device['id'], device['token'], {'agent': current['agent'], 'assets': current['assets']})
    assert nodes.list()[0]['update_state'] == 'current'
    nodes.claim(device['id'], device['token'], {'agent': 'not-hex;bad', 'assets': ''})
    assert nodes.list()[0]['agent_version'] == current['agent']  # 非法上报值忽略


def test_update_command_reads_dpapi_locally_without_token(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    command = nodes.update_command(device['id'])['command']
    script = base64.b64decode(command.split()[-1]).decode('utf-16le')
    assert device['token'] not in script and 'ProtectedData]::Unprotect' in script
    assert script.index('Stop-ScheduledTask') < script.index('Move-Item') < script.index('Start-ScheduledTask')
    nodes.revoke(device['id'])
    with pytest.raises(ValueError):
        nodes.update_command(device['id'])
