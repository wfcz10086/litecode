"""设备凭据仅在内存解密，以及独立卸载任务的派发。"""
import base64
import hashlib
import urllib.error
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib import windows_node_agent as agent


def test_dpapi_loader_does_not_rewrite_plaintext(tmp_path):
    data = {'server': 'https://example.com', 'id': 'device', 'token_dpapi': base64.b64encode(b'encrypted-blob').decode()}
    file = tmp_path / 'node.json'; file.write_text(json.dumps(data), encoding='utf-8-sig')
    with patch.object(agent, 'unprotect_token', return_value='test-secret') as unprotect:
        config = agent.load_config(tmp_path)
    unprotect.assert_called_once_with(b'encrypted-blob')
    assert config['token'] == 'test-secret'
    assert 'test-secret' not in file.read_text(encoding='utf-8-sig')
    assert 'token_dpapi' not in config


def test_plaintext_legacy_credential_requires_migration(tmp_path):
    (tmp_path / 'node.json').write_text(json.dumps({'token': 'legacy-secret'}))
    with pytest.raises(ValueError, match='升级'):
        agent.load_config(tmp_path)


def test_uninstall_uses_separate_task_without_credentials_in_command(tmp_path):
    with patch.object(agent, 'run_quiet') as run:
        run.return_value.returncode = 0
        agent.launch_uninstall(tmp_path)
    args = run.call_args.args[0]
    assert '-Schedule' in args and '-Root' in args
    assert str(tmp_path / 'uninstall.ps1') in args
    assert not any('token' in arg for arg in args)
    for code in (1, None):   # 失败或超时 (None) 都报启动失败
        run.return_value.returncode = code
        with patch.object(agent, 'run_quiet', return_value=run.return_value):
            with pytest.raises(RuntimeError, match='启动失败'):
                agent.launch_uninstall(tmp_path)


def test_uninstall_command_stops_agent_without_desktop_action(tmp_path):
    def response(config, path, data=None):
        if path == '/poll':
            return json.dumps({'job': {'id': 'test', 'action': 'uninstall', 'request': {}}}).encode()
        return b'# component'
    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value={'id': 'test'}), \
         patch.object(agent, 'request', side_effect=response), \
         patch.object(agent.os, 'chdir'), \
         patch.object(agent, 'launch_uninstall') as uninstall, \
         patch.object(agent, 'run_job') as desktop:
        agent.main()
    uninstall.assert_called_once_with(tmp_path)
    desktop.assert_not_called()


def _served(name):
    # 控制端路由 read_text() 后以 UTF-8 下发
    return (ROOT / 'lib' / name).read_text().encode()


def test_agent_and_server_hash_same_bytes(tmp_path):
    from lib.windows_nodes import component_versions, WORKER_FILES
    for asset, name in zip(agent.ASSETS, WORKER_FILES):
        (tmp_path / asset).write_bytes(_served(name))
    current = component_versions()
    assert agent.assets_version(tmp_path) == current['assets']
    assert hashlib.sha256(_served('windows_node_agent.py')).hexdigest()[:16] == current['agent']


def _run_agent(tmp_path, responses, served):
    calls = []
    def response(config, path, data=None):
        calls.append(path)
        if path == '/versions':
            raise urllib.error.HTTPError('', 404, 'none', {}, None)   # 启动时不带校验直接加载
        if path == '/poll':
            if not responses:
                # 带撤销标记的 403, 连续 3 次后代理停用自己并退出
                raise urllib.error.HTTPError('', 403, 'stop', {'X-LiteCode-Auth': 'revoked'}, None)
            return json.dumps(responses.pop(0)).encode()
        return served(path)
    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value={'id': 'a' * 32}), \
         patch.object(agent, 'request', side_effect=response), \
         patch.object(agent.time, 'sleep'), \
         patch.object(agent.os, 'chdir'), \
         patch.object(agent, 'powershell') as shell, \
         patch.object(agent.subprocess, 'Popen') as popen:
        shell.return_value.returncode = 0
        agent.main()
    _run_agent.shell = shell
    return calls, popen


def test_idle_agent_hot_reloads_worker_assets(tmp_path):
    downloads = []
    def served(path):
        downloads.append(path)   # 启动先下载 v0 全套, 之后控制端已发布 v1
        return ('# v%d %s' % (0 if len(downloads) <= len(agent.ASSETS) else 1, path)).encode()
    v1 = hashlib.sha256(b''.join(('# v1 /assets/' + a).encode() for a in agent.ASSETS)).hexdigest()[:16]
    sys.modules['windows_desktop_worker'] = object()
    first = {'job': None, 'versions': {'assets': v1, 'agent': agent.VERSIONS['agent']}}
    calls, popen = _run_agent(tmp_path, [first, dict(first)], served)
    # 启动加载一次 + 版本不同重载一次; 同一目标版本不重复下载
    assert calls.count('/assets/worker.py') == 2
    assert 'windows_desktop_worker' not in sys.modules
    assert agent.VERSIONS['assets'] == agent.assets_version(tmp_path)
    popen.assert_not_called()


def test_agent_self_update_verifies_hash_before_replace(tmp_path):
    new = b'# new agent'
    good = hashlib.sha256(new).hexdigest()[:16]
    served = lambda path: new if path == '/assets/agent.py' else b'# c'
    (tmp_path / 'agent.py').write_bytes(b'# old agent')
    # 公布的哈希与下载内容不符: 不替换、不重启, 同一版本只试一次
    bad = {'job': None, 'versions': {'agent': 'f' * 16}}
    calls, popen = _run_agent(tmp_path, [bad, dict(bad)], served)
    assert (tmp_path / 'agent.py').read_bytes() == b'# old agent'
    assert calls.count('/assets/agent.py') == 1 and not popen.called
    # 哈希一致: 原子替换并通过计划任务重启自己
    calls, popen = _run_agent(tmp_path, [{'job': None, 'versions': {'agent': good}}], served)
    assert (tmp_path / 'agent.py').read_bytes() == new
    assert "Start-ScheduledTask -TaskName 'LiteCode-Node-" + 'a' * 32 in popen.call_args.args[0][-1]


def test_agent_does_not_update_while_job_running(tmp_path):
    job = {'job': {'id': 'j', 'action': 'screenshot', 'request': {}}, 'versions': {'agent': 'e' * 16}}
    with patch.object(agent, 'run_job', return_value={'result': {}, 'images': {}}):
        calls, popen = _run_agent(tmp_path, [job], lambda p: b'# c')
    assert '/assets/agent.py' not in calls and not popen.called


def test_task_hardened_once_and_disabled_after_revoke(tmp_path):
    idle = {'job': None, 'versions': {}}
    _run_agent(tmp_path, [idle], lambda p: b'# c')
    scripts = [c.args[0] for c in _run_agent.shell.call_args_list]
    assert 'RestartCount 999' in scripts[0] and 'RepetitionInterval (New-TimeSpan -Minutes 5)' in scripts[0]
    assert "Disable-ScheduledTask -TaskName 'LiteCode-Node-" + 'a' * 32 in scripts[-1]   # 轮询 403 → 停用
    assert (tmp_path / agent.TASK_HARDENED).exists()
    _run_agent(tmp_path, [idle], lambda p: b'# c')
    assert not any('RestartCount' in c.args[0] for c in _run_agent.shell.call_args_list)  # 只修一次
