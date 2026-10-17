"""Windows 节点 shell 能力开关作为权限边界:
1. 开关状态带签名, 文件写入类工具改 nodes.json 无效 (含回写旧签名的重放);
2. 开关关闭时 launch_app 只接受内置白名单, "运行"对话框快捷键被拒; 开启后放开。"""
import asyncio
import base64
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib.windows_nodes import Nodes, component_versions
from lib import windows_desktop_actions as wda


def _enroll_online(nodes, alias='ops'):
    created = nodes.create(alias, 'https://litecode.example.com')
    code = re.search(r"-Code '([^']+)'", base64.b64decode(created['command'].split()[-1]).decode('utf-16le'))[1]
    device = nodes.redeem(code, {'computer': 'win11', 'user': 'tester'})
    nodes.claim(device['id'], device['token'], component_versions())
    return device


def _write_file_tool(path, content):
    """走真实 write_file 处理器 (模型可用的文件写入类工具)。"""
    from core.tools.handlers import file_io
    import tool_dispatch as td   # noqa: F401  (handler 内部惰性 import)
    result, _ = asyncio.run(file_io.h_write_file('t', {'filepath': str(path), 'content': content}))
    return result


def _patch_nodes_json(tmp_path, mutate):
    file = tmp_path / '.secrets/windows_nodes/nodes.json'
    data = json.loads(file.read_text())
    mutate(data)
    return file, json.dumps(data, ensure_ascii=False)


# ---------------- 1. 开关状态完整性 ----------------

def test_direct_file_edit_cannot_enable_shell(tmp_path):
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes)
    file, content = _patch_nodes_json(tmp_path, lambda d: d['ops'].update(capabilities={'shell': True}))
    file.write_text(content)
    assert nodes.node_capabilities(device['id'])['shell'] is False
    assert nodes.list()[0]['capabilities'] == {'shell': False}
    assert 'capabilities_sig' not in nodes.list()[0]
    from lib.windows_desktop import execute
    with pytest.raises(ValueError, match='shell'):
        execute({'alias': 'ops', 'action': 'run_powershell', 'script': 'whoami'}, str(tmp_path))


def test_write_file_tool_cannot_enable_shell(tmp_path, monkeypatch):
    try:
        import tool_dispatch  # noqa: F401
        from core.tools.handlers import file_io  # noqa: F401
    except Exception as exc:   # pragma: no cover - 环境缺依赖时退化为直接写文件的用例
        pytest.skip(f'tool_dispatch 不可导入: {exc}')
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes)
    file, content = _patch_nodes_json(
        tmp_path, lambda d: d['ops'].update(capabilities={'shell': True}, capabilities_sig='0' * 64))
    out = _write_file_tool(file, content)
    assert json.loads(file.read_text())['ops']['capabilities']['shell'] is True, out   # 确实写进去了
    assert nodes.node_capabilities(device['id'])['shell'] is False                   # 但不生效


def test_replayed_old_signature_is_rejected(tmp_path):
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes)
    nodes.set_capability(device['id'], 'shell', True)
    file = tmp_path / '.secrets/windows_nodes/nodes.json'
    enabled_snapshot = file.read_text()                     # 开启时抄下的记录 (含当时的签名)
    nodes.set_capability(device['id'], 'shell', False)
    file.write_text(enabled_snapshot)                       # 关闭后回写
    assert nodes.node_capabilities(device['id'])['shell'] is False


def test_signature_bound_to_node(tmp_path):
    nodes = Nodes(tmp_path)
    a = _enroll_online(nodes, 'a1')
    b = _enroll_online(nodes, 'b1')
    nodes.set_capability(a['id'], 'shell', True)
    file, content = _patch_nodes_json(tmp_path, lambda d: d['b1'].update(
        capabilities={'shell': True}, capabilities_sig=d['a1']['capabilities_sig']))
    file.write_text(content)
    assert nodes.node_capabilities(b['id'])['shell'] is False
    assert nodes.node_capabilities(a['id'])['shell'] is True


def test_key_file_is_binary_and_corrupt_key_fails_closed(tmp_path):
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes)
    nodes.set_capability(device['id'], 'shell', True)
    key = tmp_path / '.secrets/windows_nodes' / Nodes._CAP_KEY_FILE
    raw = key.read_bytes()
    assert raw.startswith(b'\x00') and key.suffix == '.bin'    # read_file 按二进制拒读
    assert (key.stat().st_mode & 0o777) == 0o600
    key.write_text('attacker-chosen-key')
    assert nodes.node_capabilities(device['id'])['shell'] is False
    nodes.set_capability(device['id'], 'shell', True)           # 管理员重新开启即恢复
    assert nodes.node_capabilities(device['id'])['shell'] is True


def test_toggle_keeps_other_nodes_and_resets_tampered(tmp_path):
    nodes = Nodes(tmp_path)
    a = _enroll_online(nodes, 'a1')
    b = _enroll_online(nodes, 'b1')
    c = _enroll_online(nodes, 'c1')
    nodes.set_capability(a['id'], 'shell', True)
    file, content = _patch_nodes_json(tmp_path, lambda d: d['c1'].update(capabilities={'shell': True}))
    file.write_text(content)
    nodes.set_capability(b['id'], 'shell', True)                # 换密钥重签
    assert nodes.node_capabilities(a['id'])['shell'] is True    # 合法开启的保留
    assert nodes.node_capabilities(b['id'])['shell'] is True
    assert nodes.node_capabilities(c['id'])['shell'] is False   # 被篡改的归零
    assert json.loads(file.read_text())['c1']['capabilities']['shell'] is False


def test_legacy_unsigned_enabled_record_reads_as_off(tmp_path):
    """升级前存的 shell=true 没有签名: 按关闭处理, 需管理员重新开启一次。"""
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes)
    file, content = _patch_nodes_json(tmp_path, lambda d: d['ops'].update(capabilities={'shell': True}))
    file.write_text(content)
    assert nodes.node_capabilities(device['id'])['shell'] is False


def test_admin_endpoint_still_toggles(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes)
    monkeypatch.setattr(router, 'manager', lambda: Nodes(tmp_path))
    monkeypatch.setattr(router, 'require_management', lambda request: None)
    app = FastAPI()
    app.include_router(router.router)
    client = TestClient(app)
    path = f"/api/windows/nodes/{device['id']}/shell"
    r = client.post(path, json={'enabled': True})
    assert r.status_code == 200 and r.json()['capabilities'] == {'shell': True}
    listed = client.get('/api/windows/nodes').json()['nodes'][0]
    assert listed['capabilities'] == {'shell': True} and 'capabilities_sig' not in listed
    poll = router._poll_reply(nodes, device['id'], None)
    assert poll['capabilities'] == {'shell': True}
    assert client.post(path, json={'enabled': False}).json()['capabilities'] == {'shell': False}
    assert nodes.node_capabilities(device['id'])['shell'] is False


# ---------------- 2. 桌面动作不能旁路启动命令行 ----------------

@pytest.mark.parametrize('name', ['notepad', 'Explorer', ' calc ', 'edge', 'chrome', 'mspaint', 'paint'])
def test_guard_allows_whitelist_when_shell_off(name):
    wda.shell_guard('launch_app', {'name': name}, False)


@pytest.mark.parametrize('name', ['Windows PowerShell ISE', 'regedit', '命令提示符', 'mmc', 'wsl', 'bash', 'WeChat'])
def test_guard_rejects_non_whitelist_when_shell_off(name):
    with pytest.raises(ValueError, match='内置应用'):
        wda.shell_guard('launch_app', {'name': name}, False)
    wda.shell_guard('launch_app', {'name': name}, True)          # 开启后放开


def test_guard_run_dialog_hotkey():
    for keys in ('win+r', 'WIN+R'):
        with pytest.raises(ValueError, match='运行'):
            wda.shell_guard('hotkey', {'keys': keys}, False)
        wda.shell_guard('hotkey', {'keys': keys}, True)
    wda.shell_guard('hotkey', {'keys': 'ctrl+c'}, False)        # 其他快捷键不受影响


def test_worker_launch_app_no_start_menu_fallback_when_off(monkeypatch):
    sent = []
    monkeypatch.setattr(wda, 'send_keyboard', lambda *a, **k: sent.append(a))
    monkeypatch.setattr(wda, 'type_unicode', lambda text: sent.append(text))
    with pytest.raises(ValueError, match='内置应用'):
        wda.launch_app({'name': 'regedit'})                      # 无 shell_allowed = 关闭
    with pytest.raises(ValueError, match='内置应用'):
        wda.launch_app({'name': 'regedit', 'shell_allowed': 'yes'})   # 只认布尔 true
    assert sent == []
    assert wda.launch_app({'name': 'regedit', 'shell_allowed': True})['method'] == 'start_menu'
    assert 'regedit' in sent
    with pytest.raises(ValueError, match='shell 能力'):
        wda.launch_app({'name': 'cmd', 'shell_allowed': True})   # cmd/PowerShell 始终走 run_powershell


def test_server_rejects_before_dispatch_when_off(tmp_path):
    from lib.windows_desktop import execute
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes)
    with patch.object(Nodes, 'job', side_effect=AssertionError('不应派发')):
        with pytest.raises(ValueError, match='内置应用'):
            execute({'alias': 'ops', 'action': 'launch_app', 'name': 'regedit'}, str(tmp_path))
        with pytest.raises(ValueError, match='运行'):
            execute({'alias': 'ops', 'action': 'hotkey', 'keys': 'win+r'}, str(tmp_path))
    nodes.set_capability(device['id'], 'shell', True)
    dispatched = []
    def fake_job(self, alias, action, request, timeout=60):
        dispatched.append((action, request))
        raise RuntimeError('stop')
    with patch.object(Nodes, 'job', fake_job):
        for args in ({'action': 'launch_app', 'name': 'regedit'}, {'action': 'hotkey', 'keys': 'win+r'}):
            with pytest.raises(RuntimeError, match='stop'):
                execute({'alias': 'ops', **args}, str(tmp_path))
    assert [a for a, _ in dispatched] == ['launch_app', 'hotkey']
    assert all('shell_allowed' not in r for _, r in dispatched)    # 服务端请求从不携带该字段


def test_ssh_path_rejects_non_whitelist_before_connecting(tmp_path):
    from lib.windows_desktop import execute
    with pytest.raises(ValueError, match='内置应用'):
        execute({'alias': 'ssh-only', 'action': 'launch_app', 'name': 'regedit'}, str(tmp_path))


def test_agent_sets_shell_allowed_from_capabilities(tmp_path):
    from lib import windows_node_agent as agent
    seen = []
    stub = types.ModuleType('windows_desktop_worker')
    stub.run = lambda action, folder, request: seen.append(request) or {'ok': True}
    with patch.dict(sys.modules, {'windows_desktop_worker': stub}):
        job = {'id': 'j1', 'action': 'launch_app', 'request': {'name': 'regedit', 'shell_allowed': True}}
        agent.run_job({'id': 'a' * 32}, tmp_path, job, capabilities={})     # 请求里伪造的字段被覆盖
        agent.run_job({'id': 'a' * 32}, tmp_path, dict(job, id='j2'), capabilities={'shell': True})
        agent.run_job({'id': 'a' * 32}, tmp_path, dict(job, id='j3'))      # 旧调用方式: 无能力 = 关闭
    assert [r['shell_allowed'] for r in seen] == [False, True, False]
    assert job['request']['shell_allowed'] is True                          # 不改动原任务对象
