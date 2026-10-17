"""主动回连 Windows 节点可靠性修复 (2026-10-17, 14 条) 的针对性测试。

Windows 端代码在 Linux 不能真跑: subprocess / psutil / pyautogui / ctypes.windll 均用 mock 或假模块。
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import types
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib import windows_desktop as wd
from lib import windows_desktop_actions as wda
from lib import windows_file_actions as wfa
from lib import windows_node_agent as agent
from lib import windows_nodes as wn
from lib import windows_system_actions as wsa
from lib import windows_transfer as wt
from lib.windows_nodes import AuthError, Nodes, component_versions, WORKER_FILES
from tests.test_windows_nodes import complete_initial, enroll


def _png(size=(40, 30), color='blue'):
    raw = io.BytesIO()
    Image.new('RGB', size, color).save(raw, 'PNG')
    return raw.getvalue()


def _app(nodes):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import windows_nodes_router as router
    app = FastAPI()
    app.include_router(router.router)
    return router, TestClient(app)


# ---------------- 1. 认证失败才 403 + 损坏任务文件隔离 + 代理只认带标记的连续 403 ----------------

def test_corrupt_job_file_is_quarantined_and_other_nodes_keep_working(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, a = enroll(nodes, 'node-a')
    _, _, b = enroll(nodes, 'node-b')
    (nodes.root / 'job-corrupt.json').write_text('{"node_id": "x", "sta')   # 写到一半的损坏文件
    (nodes.root / 'job-noid.json').write_text('[1, 2]')                     # 合法 JSON 但不是任务
    assert nodes.claim(a['id'], a['token'])['action'] == 'screenshot'        # 不抛错, 照常领取
    assert nodes.claim(b['id'], b['token'])['action'] == 'screenshot'
    assert not (nodes.root / 'job-corrupt.json').exists()
    quarantined = sorted(p.name.split('.json')[0] for p in (nodes.root / 'quarantine').iterdir())
    assert quarantined == ['job-corrupt', 'job-noid']
    assert len(nodes.list()) == 2


def test_auth_error_is_value_error_subclass_for_old_callers(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    with pytest.raises(AuthError):
        nodes.authenticate(device['id'], 'wrong')
    with pytest.raises(AuthError):
        nodes.claim('f' * 32, device['token'])        # 节点记录已删除
    nodes.revoke(device['id'])
    with pytest.raises(AuthError):
        nodes.claim(device['id'], device['token'])
    assert issubclass(AuthError, ValueError)


def test_poll_maps_only_auth_failure_to_marked_403(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    router, client = _app(nodes)
    prefix = '/api/windows/device/' + device['id']
    headers = {'Authorization': 'Bearer ' + device['token']}
    with patch.object(router, 'manager', return_value=nodes):
        bad = client.get(prefix + '/poll', headers={'Authorization': 'Bearer wrong'})
        assert bad.status_code == 403 and bad.headers['x-litecode-auth'] == 'revoked'
        # 非认证类异常 (含 JSONDecodeError 这种 ValueError 子类) 一律 500, 不带撤销标记
        for error in (json.JSONDecodeError('坏', '{', 0), ValueError('其他校验'), OSError('磁盘')):
            with patch.object(Nodes, 'claim', side_effect=error):
                response = client.get(prefix + '/poll', headers=headers)
            assert response.status_code == 500 and 'x-litecode-auth' not in response.headers
        ok = client.get(prefix + '/poll', headers=headers).json()
        assert ok['job']['action'] == 'screenshot'
        assert set(ok['asset_hashes']) == set(WORKER_FILES)          # 新字段, 旧代理忽略
        assert ok['versions'] == component_versions()
        status = client.post(prefix + '/status', headers={'Authorization': 'Bearer wrong'}, json={'state': 'online'})
        assert status.status_code == 403 and status.headers['x-litecode-auth'] == 'revoked'
        assert client.get(prefix + '/versions', headers=headers).json()['versions'] == component_versions()


def _agent_main(tmp_path, outcomes):
    """按顺序回放 /poll 结果 (dict=成功, int=HTTP 错误码, (code, headers)); 用完后抛 KeyboardInterrupt 结束。"""
    def response(config, path, data=None):
        if path == '/versions':
            raise urllib.error.HTTPError('', 404, 'none', {}, None)
        if path != '/poll':
            return b'# c'
        if not outcomes:
            raise KeyboardInterrupt
        item = outcomes.pop(0)
        if isinstance(item, dict):
            return json.dumps(item).encode()
        code, hdrs = item if isinstance(item, tuple) else (item, {})
        raise urllib.error.HTTPError('', code, 'x', hdrs, None)
    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value={'id': 'a' * 32}), \
         patch.object(agent, 'request', side_effect=response), \
         patch.object(agent.time, 'sleep'), patch.object(agent.os, 'chdir'), \
         patch.object(agent, 'harden_task'), patch.object(agent, 'disable_task') as disable:
        try:
            agent.main()
            stopped = True
        except KeyboardInterrupt:
            stopped = False
    return stopped, disable


def test_agent_ignores_unmarked_403_and_5xx(tmp_path):
    marked = (403, {'X-LiteCode-Auth': 'revoked'})
    stopped, disable = _agent_main(tmp_path, [403, 403, 403, 403, 500, 502, marked, marked])
    assert not stopped and not disable.called            # 裸 403 (反代/WAF) 与 5xx 按网络错误退避


def test_agent_disables_only_after_three_consecutive_marked_403(tmp_path):
    marked = (403, {'X-LiteCode-Auth': 'revoked'})
    idle = {'job': None, 'versions': {}}
    # 中间夹一次成功轮询, 计数清零
    stopped, disable = _agent_main(tmp_path, [marked, marked, idle, marked, marked])
    assert not stopped and not disable.called
    stopped, disable = _agent_main(tmp_path, [marked, marked, marked])
    assert stopped and disable.called


def test_write_is_fsynced_and_atomic(tmp_path):
    target = tmp_path / 'x' / 'nodes.json'
    with patch.object(wn.os, 'fsync', wraps=os.fsync) as fsync:
        wn._write(target, {'a': 1})
    assert fsync.called and json.loads(target.read_text()) == {'a': 1}
    with patch.object(wn.os, 'replace', side_effect=OSError('满')):
        with pytest.raises(OSError):
            wn._write(target, {'a': 2})
    assert json.loads(target.read_text()) == {'a': 1}
    assert [p.name for p in target.parent.iterdir()] == ['nodes.json']   # 失败时临时文件已清理


# ---------------- 2. PowerShell 超时结束进程树, 一定按时返回 ----------------

def _fake_subprocess(hang=True):
    fake = types.ModuleType('subprocess')
    fake.CREATE_NO_WINDOW = 0
    fake.DEVNULL = -3
    fake.TimeoutExpired = type('TimeoutExpired', (Exception,), {})
    fake.CompletedProcess = lambda args, code: types.SimpleNamespace(args=args, returncode=code)
    fake.calls = []

    class Popen:
        pid = 4242

        def __init__(self, cmd, **kwargs):
            self.waits = 0
            out = kwargs.get('stdout')
            if hasattr(out, 'write'):
                out.write(b'partial output')
                out.flush()

        def wait(self, timeout=None):
            self.waits += 1
            if hang and self.waits == 1:
                raise fake.TimeoutExpired()
            return 0

        def kill(self):
            fake.calls.append('kill')
    fake.Popen = Popen
    fake.run = lambda cmd, **kwargs: fake.calls.append(cmd)
    return fake


def test_run_powershell_timeout_kills_process_tree(monkeypatch):
    fake = _fake_subprocess(hang=True)
    monkeypatch.setitem(sys.modules, 'subprocess', fake)
    out = wsa.run_powershell({'script': 'Start-Process notepad; Start-Sleep 999', 'timeout': 1})
    assert ['taskkill', '/T', '/F', '/PID', '4242'] in fake.calls
    assert out['timed_out'] is True and out['ok'] is False and out['state'] == 'run_powershell_timeout'
    assert out['stdout'] == 'partial output'               # 超时前已有的输出保留


def test_run_bounded_returns_on_time_when_grandchild_holds_output():
    """真实进程: 孙进程继承输出句柄并长时间存活, 旧实现 communicate() 会一直等它; 现在按时返回。"""
    started = time.monotonic()
    code, out, err, timed_out = wsa.run_bounded(['sh', '-c', 'echo hi; sleep 30 & sleep 30'], timeout=1)
    assert time.monotonic() - started < 1 + wsa.KILL_GRACE
    assert timed_out and code is None and out.strip() == b'hi'


def test_agent_powershell_helper_also_bounded(monkeypatch):
    fake = _fake_subprocess(hang=True)
    monkeypatch.setattr(agent, 'subprocess', fake)
    result = agent.powershell('Disable-ScheduledTask x', timeout=1)
    assert result.returncode is None and ['taskkill', '/T', '/F', '/PID', '4242'] in fake.calls
    monkeypatch.setattr(agent, 'subprocess', _fake_subprocess(hang=False))
    assert agent.powershell('x').returncode == 0


# ---------------- 3. 按名称结束进程: 受保护名单 / 多进程 / 会话 0 ----------------

class _Proc:
    def __init__(self, pid, name):
        self.pid, self._name, self.info, self.terminated = pid, name, {'pid': pid, 'name': name}, False

    def name(self):
        return self._name

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True

    def parent(self):
        return None


def _psutil(procs):
    module = types.ModuleType('psutil')
    module.Error = type('Error', (Exception,), {})
    module.NoSuchProcess = type('NoSuchProcess', (module.Error,), {})
    module.TimeoutExpired = type('TimeoutExpired', (module.Error,), {})
    module.process_iter = lambda fields=None: list(procs)
    by_pid = {p.pid: p for p in procs}
    module.Process = lambda pid=None: by_pid.get(pid) or _Proc(pid or os.getpid(), 'python.exe')
    return module


@pytest.mark.parametrize('name', ['SVCHOST.EXE', 'svchost', 'Dwm.exe', 'msmpeng', 'RuntimeBroker.exe',
                                  'ctfmon', 'sihost.EXE', 'fontdrvhost', 'spoolsv', 'taskhostw'])
def test_protected_process_case_and_suffix_variants_refused(monkeypatch, name):
    proc = _Proc(500, name if name.lower().endswith('.exe') else name + '.exe')
    monkeypatch.setitem(sys.modules, 'psutil', _psutil([proc]))
    monkeypatch.setattr(wsa, '_session_id', lambda pid: 1)
    out = wsa.kill_process({'name': name, 'force': True})
    assert out['killed'] == [] and '关键' in out['skipped'][0]['reason'] and not proc.terminated


def test_name_matching_multiple_processes_requires_pid_or_all(monkeypatch):
    procs = [_Proc(301, 'chrome.exe'), _Proc(302, 'Chrome.exe'), _Proc(303, 'notepad.exe')]
    monkeypatch.setitem(sys.modules, 'psutil', _psutil(procs))
    monkeypatch.setattr(wsa, '_session_id', lambda pid: 1)
    out = wsa.kill_process({'name': 'chrome'})
    assert out['ok'] is False and out['matches'] == [301, 302] and '301' in out['error']
    assert not any(p.terminated for p in procs)
    out = wsa.kill_process({'name': 'chrome.exe', 'all': True})
    assert [k['pid'] for k in out['killed']] == [301, 302]
    assert wsa.kill_process({'name': 'NOTEPAD'})['killed'][0]['pid'] == 303   # 单个命中照常结束


def test_session_zero_service_process_refused(monkeypatch):
    proc = _Proc(700, 'someservice.exe')
    monkeypatch.setitem(sys.modules, 'psutil', _psutil([proc]))
    monkeypatch.setattr(wsa, '_session_id', lambda pid: 0)
    out = wsa.kill_process({'pid': 700})
    assert out['killed'] == [] and '会话 0' in out['skipped'][0]['reason'] and not proc.terminated


def test_kill_process_all_flag_passed_through_request(tmp_path):
    assert wd.desktop_request({'action': 'kill_process', 'name': 'chrome', 'all': True}, tmp_path)['all'] is True
    assert 'all' not in wd.desktop_request({'action': 'kill_process', 'name': 'chrome'}, tmp_path)


# ---------------- 4. upload 相对路径按工作区解析 ----------------

def test_upload_relative_source_resolves_against_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / 'ws'
    (workspace / 'out').mkdir(parents=True)
    (workspace / 'out' / 'a.txt').write_text('x')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)                       # 进程 cwd 不是工作区
    assert wt.resolve_within(workspace, 'out/a.txt') == (workspace / 'out' / 'a.txt').resolve()
    with pytest.raises(ValueError):
        wt.resolve_within(workspace, '../elsewhere/b.txt')


# ---------------- 5. 遍历任务时容忍并发删除 ----------------

def test_job_scan_tolerates_concurrently_deleted_files(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    ghost = nodes.root / 'job-ghost.json'
    real_glob = Path.glob

    def glob(self, pattern):
        found = list(real_glob(self, pattern))
        return found + [ghost] if pattern == 'job-*.json' else found   # 列出后已被 wait() 删除
    with patch.object(Path, 'glob', glob):
        assert nodes.claim(device['id'], device['token'])['action'] == 'screenshot'
        assert len(nodes.list()) == 1
        nodes.revoke(device['id'])
    assert not (nodes.root / 'quarantine').exists()


# ---------------- 6. pyautogui FAILSAFE 关闭 ----------------

def test_pyautogui_failsafe_disabled_for_desktop_and_ocr(monkeypatch, tmp_path):
    import ctypes
    from lib import windows_desktop_ocr as ocr
    fake = types.ModuleType('pyautogui')
    fake.FAILSAFE = True
    fake.click = MagicMock()
    monkeypatch.setitem(sys.modules, 'pyautogui', fake)
    assert wda.pyautogui_module().FAILSAFE is False
    fake.FAILSAFE = True
    monkeypatch.setattr(ctypes, 'windll', MagicMock(), raising=False)
    monkeypatch.setattr(ocr, 'capture_ocr', lambda folder, name='screen.png': {
        'lines': [], 'width': 10, 'height': 10, 'language': 'zh', 'ocr_scale': 1, 'origin': [0, 0]})
    ocr.perform('ocr', tmp_path, {})
    assert fake.FAILSAFE is False


# ---------------- 7. 组件更新全有或全无 ----------------

def _serve(contents):
    return lambda config, path, data=None: contents[path.rsplit('/', 1)[-1]]


def _served_name(asset):
    return {'worker.py': 'windows_desktop_worker.py', 'uninstall.ps1': 'windows_node_uninstall.ps1'}.get(asset, asset)


def test_component_update_replaces_nothing_on_any_hash_mismatch(tmp_path):
    for asset in agent.ASSETS:
        (tmp_path / asset).write_bytes(b'# old ' + asset.encode())
    new = {asset: b'# new ' + asset.encode() for asset in agent.ASSETS}
    hashes = {_served_name(a): hashlib.sha256(d).hexdigest() for a, d in new.items()}
    hashes['windows_file_actions.py'] = '0' * 64                      # 中间某个文件不符
    with patch.object(agent, 'request', side_effect=_serve(new)):
        with pytest.raises(ValueError, match='windows_file_actions'):
            agent.load_assets({}, tmp_path, None, hashes)
    assert all((tmp_path / a).read_bytes() == b'# old ' + a.encode() for a in agent.ASSETS)
    assert not (tmp_path / '.update').exists()
    # 下载中途网络失败同样一个都不替换
    def flaky(config, path, data=None):
        if path.endswith('windows_transfer.py'):
            raise urllib.error.URLError('断网')
        return new[path.rsplit('/', 1)[-1]]
    with patch.object(agent, 'request', side_effect=flaky):
        with pytest.raises(urllib.error.URLError):
            agent.load_assets({}, tmp_path)
    assert all((tmp_path / a).read_bytes() == b'# old ' + a.encode() for a in agent.ASSETS)
    # 整体版本不符也不替换
    hashes['windows_file_actions.py'] = hashlib.sha256(new['windows_file_actions.py']).hexdigest()
    with patch.object(agent, 'request', side_effect=_serve(new)):
        with pytest.raises(ValueError):
            agent.load_assets({}, tmp_path, 'f' * 16, hashes)
    assert (tmp_path / 'worker.py').read_bytes() == b'# old worker.py'


def test_component_update_success_replaces_all_and_bom_for_ps1(tmp_path):
    served = {asset: (ROOT / 'lib' / name).read_text().encode() for asset, name in zip(agent.ASSETS, WORKER_FILES)}
    from lib.windows_nodes import asset_hashes
    with patch.object(agent, 'request', side_effect=_serve(served)):
        agent.load_assets({}, tmp_path, component_versions()['assets'], asset_hashes())
    assert agent.VERSIONS['assets'] == component_versions()['assets'] == agent.assets_version(tmp_path)
    assert (tmp_path / 'uninstall.ps1').read_bytes().startswith(agent.BOM)   # PowerShell 5.1 才能正确读中文
    assert (tmp_path / 'windows_desktop_worker.py').read_bytes() == served['worker.py']


# ---------------- 8. 中断上传残留的 .part 清理 ----------------

def test_stale_part_files_cleaned_when_new_upload_starts(tmp_path, monkeypatch):
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    folder = wfa.downloads_dir()
    old, fresh = folder / ('.part-' + 'a' * 32), folder / ('.part-' + 'b' * 32)
    old.write_bytes(b'x')
    fresh.write_bytes(b'y')
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    data = b'hello'
    out = wfa.upload({'transfer_id': 'c' * 32, 'name': 'h.txt', 'index': 0, 'total': 1,
                      'sha256': wt.sha256_hex(data), 'data': base64.b64encode(data).decode()})
    assert out['state'] == 'upload_done'
    assert not old.exists() and fresh.exists()           # 进行中的传输 (mtime 新) 不误删


# ---------------- 9. 只有带截图的结果改变桌面状态 ----------------

def test_only_results_with_screenshot_change_desktop_state(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    complete_initial(nodes, device)
    assert nodes.list()[0]['desktop_ready'] is True
    for action, result in (('system_info', {'ok': True, 'state': 'system_info'}),
                           ('list_dir', {'ok': False, 'state': 'error', 'error': '目录不存在'})):
        job = nodes.job('test-node', action, {})
        nodes.claim(device['id'], device['token'])
        nodes.finish(device['id'], device['token'], job, result, {})
        node = nodes.list()[0]
        assert node['desktop_ready'] is True and node['desktop_state'] == 'desktop_ready'
    job = nodes.job('test-node', 'screenshot', {})
    nodes.claim(device['id'], device['token'])
    nodes.finish(device['id'], device['token'], job, {'ok': False, 'state': 'focus_failed', 'image': 'screen.png'},
                 {'screen.png': base64.b64encode(_png()).decode()})
    node = nodes.list()[0]
    assert node['desktop_state'] == 'focus_failed' and node['desktop_ready'] is False


# ---------------- 10. 只读路径不回写 + 心跳节流 ----------------

def test_read_paths_do_not_rewrite_and_heartbeat_is_throttled(tmp_path):
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes)
    nodes.claim(device['id'], device['token'])            # 上线 + 领走首张截图任务
    with patch.object(wn, '_write', wraps=wn._write) as write:
        nodes.list()
        nodes.node_capabilities(device['id'])
        nodes.authenticate(device['id'], device['token'])
        for _ in range(3):
            assert nodes.claim(device['id'], device['token']) is None   # 空闲长轮询每秒一次
        assert write.call_count == 0
        with nodes.locked() as data:
            data['test-node']['last_seen'] = time.time() - wn.HEARTBEAT_WRITE_INTERVAL - 1
        write.reset_mock()
        nodes.claim(device['id'], device['token'])
        assert write.call_count == 1                       # 超过 15 秒才刷新 last_seen
    assert time.time() - nodes.list()[0]['last_seen'] < 5


def test_router_offloads_blocking_work_to_threads():
    import inspect
    from routers import windows_nodes_router as router
    for handler in (router.poll, router.result, router.list_nodes):
        assert 'asyncio.to_thread' in inspect.getsource(handler)


# ---------------- 11. 卸载脚本进入更新清单, 三处清单一致 ----------------

def test_dispatch_manifests_are_consistent():
    from routers import windows_nodes_router as router
    assert 'uninstall.ps1' in agent.ASSETS
    assert set(router.ASSETS) == set(agent.ASSETS) | {'agent.py'}          # 路由能下发代理要的每个文件
    assert tuple(router.ASSETS[a] for a in agent.ASSETS) == WORKER_FILES   # 同序, 两端版本哈希一致
    assert all((ROOT / 'lib' / name).is_file() for name in router.ASSETS.values())


# ---------------- 12. 大截图自动转 JPEG ----------------

def test_oversized_screenshot_is_compressed_before_upload(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'windows_desktop_actions', wda)
    image = Image.frombytes('RGB', (2400, 1500), os.urandom(2400 * 1500 * 3))   # 噪声图, PNG 压不动
    image.save(tmp_path / 'screen.png', 'PNG')
    size = (tmp_path / 'screen.png').stat().st_size
    result = {'ok': True, 'state': 'action_executed', 'image': 'screen.png'}
    agent.shrink_large_images(tmp_path, result, limit=size - 1)
    assert result['screen_size'] == [2400, 1500] and result['auto_compressed'] is True
    assert (tmp_path / 'screen.png').read_bytes()[:2] == b'\xff\xd8'      # JPEG
    assert (tmp_path / 'screen.png').stat().st_size < size
    untouched = {'ok': True, 'image': 'screen.png'}
    agent.shrink_large_images(tmp_path, untouched)                       # 已在上限内: 不动
    assert 'screen_size' not in untouched
    # 控制端照常接收, 并写 .screen 旁车供点击坐标换算
    nodes = Nodes(tmp_path / 'ws')
    _, _, device = enroll(nodes)
    task = nodes.claim(device['id'], device['token'])
    nodes.finish(device['id'], device['token'], task['id'], dict(result),
                 {'screen.png': base64.b64encode((tmp_path / 'screen.png').read_bytes()).decode()})
    sidecar = next((tmp_path / 'ws' / 'uploads').glob('*.screen'))
    assert json.loads(sidecar.read_text()) == [2400, 1500]


# ---------------- 13. 工作目录不再是 System32 ----------------

def test_working_directory_fixes(tmp_path, monkeypatch):
    bootstrap = (ROOT / 'lib' / 'windows_node_bootstrap.ps1').read_text()
    assert 'New-ScheduledTaskAction' in bootstrap and '-WorkingDirectory $root' in bootstrap
    # 代理启动时切到用户主目录 (兼容已安装节点)
    with patch.object(sys, 'argv', ['agent.py', str(tmp_path)]), \
         patch.object(agent, 'load_config', return_value={'id': 'a' * 32}), \
         patch.object(agent, 'startup_assets', side_effect=KeyboardInterrupt), \
         patch.object(agent.os, 'chdir') as chdir:
        with pytest.raises(KeyboardInterrupt):
            agent.main()
    chdir.assert_called_once_with(Path.home())
    # launch_app 的 cwd 是"文档", 不存在则主目录
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    import subprocess
    with patch.object(subprocess, 'Popen') as popen:
        wda.launch_app({'name': 'notepad'})
        assert popen.call_args.kwargs['cwd'] == str(tmp_path)
        (tmp_path / 'Documents').mkdir()
        wda.launch_app({'name': 'notepad'})
        assert popen.call_args.kwargs['cwd'] == str(tmp_path / 'Documents')


# ---------------- 14. 节点更新中: 等待后下发, 超时才报错 ----------------

def _online(nodes, versions, alias='ops'):
    _, _, device = enroll(nodes, alias)
    nodes.claim(device['id'], device['token'], versions)
    return device


def test_action_waits_for_update_then_dispatches(tmp_path, monkeypatch):
    nodes = Nodes(tmp_path)
    device = _online(nodes, {'agent': '0' * 16, 'assets': '0' * 16})
    assert nodes.list()[0]['update_state'] == 'updating'
    monkeypatch.setattr(wd, 'UPDATE_POLL_SECONDS', 0.05)
    dispatched = []
    monkeypatch.setattr(wd, '_run_node_job', lambda n, alias, action, request, timeout, idempotent=False:
                        dispatched.append(action) or {'ok': True, 'state': action})

    def finish_update():
        time.sleep(0.3)
        nodes.heartbeat(device['id'], device['token'], versions=component_versions())   # 新代理回连
    worker = threading.Thread(target=finish_update)
    worker.start()
    started = time.monotonic()
    out = wd.execute({'alias': 'ops', 'action': 'system_info'}, str(tmp_path))
    worker.join()
    assert out['ok'] and dispatched == ['system_info'] and 0.2 < time.monotonic() - started < 5


def test_update_wait_times_out_with_clear_message(tmp_path, monkeypatch):
    nodes = Nodes(tmp_path)
    _online(nodes, {'agent': '0' * 16, 'assets': '0' * 16})
    monkeypatch.setattr(wd, 'UPDATE_POLL_SECONDS', 0.05)
    states = iter(['updating'] * 3)
    fake = types.SimpleNamespace(list=lambda: [{'alias': 'ops', 'update_state': next(states, 'updating')}])
    started = time.monotonic()
    with pytest.raises(ValueError, match='节点正在更新，请稍后重试'):
        wd.wait_until_updated(fake, 'ops', {'alias': 'ops', 'update_state': 'updating'}, limit=0.3)
    assert time.monotonic() - started < 2
