"""Windows 节点运维扩展：系统运维、文件传输、shell 开关、截图压缩、旧代理兼容。

Windows 专属库 (ctypes.windll / pyautogui / mss / psutil / subprocess) 在 Linux 不可真跑，
这里用 sys.modules 注入假模块或 mock 测纯逻辑。
"""
import os
import base64
import io
import json
from pathlib import Path
import re
import sys
import types
from unittest.mock import patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib import windows_transfer as wt
from lib import windows_file_actions as wfa
from lib import windows_system_actions as wsa
from lib.windows_nodes import Nodes, component_versions, WORKER_FILES


# ---------------- 纯传输逻辑：分块 + sha256 + 文件名 + 路径防穿越 ----------------

def test_reassemble_verifies_order_and_sha256():
    data = b'LiteCode-' + b'x' * 100
    digest = wt.sha256_hex(data)
    chunks = {0: data[:40], 1: data[40:]}
    assert wt.reassemble(chunks, digest) == data
    with pytest.raises(ValueError, match='缺失'):
        wt.reassemble({1: data[40:]}, digest)   # 缺 0 号块
    with pytest.raises(ValueError, match='sha256'):
        wt.reassemble(chunks, 'f' * 64)          # 哈希不符


def test_chunk_count_enforces_size_cap():
    assert wt.chunk_count(0) == 1
    assert wt.chunk_count(wt.CHUNK + 1) == 2
    with pytest.raises(ValueError, match='50MB'):
        wt.chunk_count(wt.MAX_TRANSFER + 1)


def test_sanitize_download_name_blocks_traversal_and_reserved():
    assert wt.sanitize_download_name('../../etc/passwd') == 'passwd'
    assert wt.sanitize_download_name(r'..\..\windows\system32\evil.exe') == 'evil.exe'
    assert wt.sanitize_download_name('a<b>:c?.txt') == 'a_b__c_.txt'
    assert wt.sanitize_download_name('   ') == 'download.bin'
    assert wt.sanitize_download_name('CON.txt').startswith('_')


def test_unique_target_never_overwrites(tmp_path):
    (tmp_path / 'report.pdf').write_text('a')
    assert wt.unique_target(tmp_path, 'report.pdf').name == 'report (1).pdf'
    (tmp_path / 'report (1).pdf').write_text('b')
    assert wt.unique_target(tmp_path, 'report.pdf').name == 'report (2).pdf'


def test_resolve_within_rejects_outside_workspace(tmp_path):
    base = tmp_path / 'ws'
    base.mkdir()
    (base / 'ok.txt').write_text('x')
    assert wt.resolve_within(base, base / 'ok.txt').name == 'ok.txt'
    with pytest.raises(ValueError, match='工作区'):
        wt.resolve_within(base, tmp_path / 'secret.txt')


# ---------------- upload 源文件收紧到 WORKSPACE/uploads/ ----------------

def _mk_workspace_with_uploads(tmp_path):
    """造一个工作区: uploads/ 放可上传文件, .secrets/ 与 sessions/ 放内部敏感文件。"""
    ws = tmp_path / 'ws'
    (ws / 'uploads').mkdir(parents=True)
    (ws / 'uploads' / 'report.pdf').write_bytes(b'ok-to-send')
    (ws / '.secrets').mkdir()
    (ws / '.secrets' / 'windows_credentials.json').write_text('{"pw":"s3cr3t"}')
    (ws / 'sessions').mkdir()
    (ws / 'sessions' / 'MEMORY.md').write_text('private')
    return ws


def test_resolve_upload_source_accepts_file_in_uploads(tmp_path):
    ws = _mk_workspace_with_uploads(tmp_path)
    got = wt.resolve_upload_source(ws, ws / 'uploads' / 'report.pdf')
    assert got == (ws / 'uploads' / 'report.pdf').resolve()


def test_resolve_upload_source_resolves_relative_against_uploads(tmp_path, monkeypatch):
    ws = _mk_workspace_with_uploads(tmp_path)
    monkeypatch.chdir(tmp_path)                 # 进程 cwd 不是工作区/uploads
    # 相对路径按 uploads/ 解析, 不按 cwd, 也不按工作区根
    assert wt.resolve_upload_source(ws, 'report.pdf') == (ws / 'uploads' / 'report.pdf').resolve()


def test_resolve_upload_source_rejects_workspace_root_file(tmp_path):
    ws = _mk_workspace_with_uploads(tmp_path)
    (ws / 'config.json').write_text('{}')
    with pytest.raises(ValueError, match='uploads'):
        wt.resolve_upload_source(ws, ws / 'config.json')


def test_resolve_upload_source_rejects_internal_subdirs(tmp_path):
    ws = _mk_workspace_with_uploads(tmp_path)
    for inside in (ws / '.secrets' / 'windows_credentials.json', ws / 'sessions' / 'MEMORY.md'):
        with pytest.raises(ValueError, match='uploads'):
            wt.resolve_upload_source(ws, inside)


def test_resolve_upload_source_rejects_outside_absolute(tmp_path):
    ws = _mk_workspace_with_uploads(tmp_path)
    outside = tmp_path / 'outside.txt'
    outside.write_text('x')
    with pytest.raises(ValueError, match='uploads'):
        wt.resolve_upload_source(ws, outside)


def test_resolve_upload_source_rejects_dotdot_traversal(tmp_path):
    ws = _mk_workspace_with_uploads(tmp_path)
    # 从 uploads/ 用 ../ 穿越回工作区根下的敏感文件
    with pytest.raises(ValueError, match='uploads'):
        wt.resolve_upload_source(ws, '../.secrets/windows_credentials.json')


def test_resolve_upload_source_rejects_symlink_escape(tmp_path):
    ws = _mk_workspace_with_uploads(tmp_path)
    link = ws / 'uploads' / 'leak.json'
    link.symlink_to(ws / '.secrets' / 'windows_credentials.json')   # uploads 内符号链接指向外部
    with pytest.raises(ValueError, match='uploads'):
        wt.resolve_upload_source(ws, link)


# ---------------- Windows 端文件动作：list_dir 上限、upload 清洗/不覆盖 ----------------

def test_list_dir_caps_at_500(tmp_path):
    for i in range(510):
        (tmp_path / f'f{i:04d}.txt').write_text('x')
    result = wfa.list_dir({'path': str(tmp_path)})
    assert result['count'] == 500 and result['truncated'] is True
    assert all(set(e) >= {'name', 'is_dir', 'size', 'modified'} for e in result['entries'])


def test_upload_sanitizes_name_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    payload = b'hello-\xe4\xb8\xad\xe6\x96\x87'
    digest = wt.sha256_hex(payload)
    args = {'transfer_id': 'deadbeef', 'name': r'..\..\evil.txt', 'index': 0, 'total': 1,
            'sha256': digest, 'data': base64.b64encode(payload).decode()}
    first = wfa.upload(dict(args))
    target = tmp_path / 'Downloads' / 'LiteCode' / 'evil.txt'
    assert first['ok'] and Path(first['path']) == target and target.read_bytes() == payload
    second = wfa.upload({**args, 'transfer_id': 'cafebabe'})
    assert Path(second['path']).name == 'evil (1).txt'   # 同名不覆盖
    assert target.read_bytes() == payload


def test_upload_rejects_bad_sha_and_transfer_id(tmp_path, monkeypatch):
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    with pytest.raises(ValueError, match='transfer_id'):
        wfa.upload({'transfer_id': 'xyz', 'name': 'a', 'index': 0, 'total': 1, 'sha256': 'f' * 64, 'data': ''})
    with pytest.raises(ValueError, match='sha256'):
        wfa.upload({'transfer_id': 'abcdef12', 'name': 'a', 'index': 0, 'total': 1,
                    'sha256': wt.sha256_hex(b'A'), 'data': base64.b64encode(b'B').decode()})


def test_download_meta_and_chunk_roundtrip(tmp_path):
    source = tmp_path / 'big.bin'
    blob = bytes(range(256)) * 5000   # ~1.25MB
    source.write_bytes(blob)
    meta = wfa.download({'path': str(source)})
    assert meta['state'] == 'download_meta' and meta['sha256'] == wt.sha256_hex(blob)
    chunks = {}
    for i in range(meta['chunks']):
        part = wfa.download({'path': str(source), 'index': i})
        chunks[i] = base64.b64decode(part['chunk'])
    assert wt.reassemble(chunks, meta['sha256']) == blob


# ---------------- Windows 端系统动作：kill 黑名单、run_powershell 解码 ----------------

class _FakeProc:
    def __init__(self, pid, name):
        self.pid = pid
        self._name = name
        self.info = {'pid': pid, 'name': name}
        self.terminated = False
    def name(self):
        return self._name
    def parent(self):
        return None
    def terminate(self):
        self.terminated = True
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self.terminated = True


def _fake_psutil(procs):
    module = types.ModuleType('psutil')
    module.Error = type('Error', (Exception,), {})
    module.NoSuchProcess = type('NoSuchProcess', (module.Error,), {})
    module.TimeoutExpired = type('TimeoutExpired', (module.Error,), {})
    module.process_iter = lambda fields=None: list(procs)
    import os as _os
    module.Process = lambda pid=None: _FakeProc(pid or _os.getpid(), 'python.exe')
    return module


def test_kill_process_blacklist_and_force(monkeypatch):
    procs = [_FakeProc(101, 'lsass.exe'), _FakeProc(102, 'explorer.exe'),
             _FakeProc(103, 'notepad.exe')]
    fake = _fake_psutil(procs)
    monkeypatch.setitem(sys.modules, 'psutil', fake)
    # 按名结束关键进程 → 拒绝
    out = wsa.kill_process({'name': 'lsass.exe'})
    assert out['killed'] == [] and out['skipped'][0]['reason'].startswith('关键')
    # explorer 不带 force → 拒绝；带 force → 结束
    assert wsa.kill_process({'name': 'explorer.exe'})['killed'] == []
    assert wsa.kill_process({'name': 'explorer.exe', 'force': True})['killed'][0]['name'] == 'explorer.exe'
    # 普通进程直接结束
    assert wsa.kill_process({'name': 'notepad.exe'})['killed'][0]['pid'] == 103


def test_kill_process_refuses_self(monkeypatch):
    import os
    me = _FakeProc(os.getpid(), 'python.exe')
    monkeypatch.setitem(sys.modules, 'psutil', _fake_psutil([me]))
    out = wsa.kill_process({'pid': os.getpid()})
    assert out['killed'] == [] and '代理自身' in out['skipped'][0]['reason']


def test_kill_process_requires_exactly_one_selector():
    with pytest.raises(ValueError):
        wsa.kill_process({})
    with pytest.raises(ValueError):
        wsa.kill_process({'pid': 1, 'name': 'x'})


def _fake_popen_module(stdout, captured=None):
    """run_powershell 改用 Popen + 临时文件收输出: 假 Popen 把 stdout 写进传入的文件。"""
    fake = types.ModuleType('subprocess')
    fake.CREATE_NO_WINDOW = 0
    fake.DEVNULL = -3
    fake.TimeoutExpired = type('TimeoutExpired', (Exception,), {})

    class _Popen:
        pid = 4242

        def __init__(self, cmd, **kwargs):
            if captured is not None:
                captured['cmd'], captured['cwd'] = cmd, kwargs.get('cwd')
                if '-File' in cmd:     # 脚本走临时文件: 运行时读出内容供断言 (运行后文件即删除)
                    captured['script'] = open(cmd[cmd.index('-File') + 1], encoding='utf-8-sig').read()
            kwargs['stdout'].write(stdout)
            kwargs['stdout'].flush()

        def wait(self, timeout=None):
            return 0
    fake.Popen = _Popen
    return fake


def test_run_powershell_sets_encoding_prefix_cwd_and_decodes_gbk(tmp_path, monkeypatch):
    captured = {}
    fake = _fake_popen_module('目录列表'.encode('gbk'), captured)    # 中文 Windows 默认 GBK 输出
    monkeypatch.setitem(sys.modules, 'subprocess', fake)
    out = wsa.run_powershell({'script': 'Get-ChildItem', 'cwd': str(tmp_path)})
    assert out['ok'] and out['exit_code'] == 0 and out['stdout'] == '目录列表'
    assert isinstance(out['duration_ms'], int) and out['cwd'] == str(tmp_path)
    assert '-Command' not in captured['cmd'] and captured['cmd'][-2] == '-File'   # 不经命令行传脚本 (防 Defender 误判)
    assert captured['script'].startswith(wsa.PS_ENCODING_PREFIX) and 'Get-ChildItem' in captured['script']
    assert not os.path.exists(captured['cmd'][-1])                                  # 临时脚本已删除
    assert captured['cwd'] == str(tmp_path)


def test_run_powershell_rejects_missing_cwd(monkeypatch):
    fake = types.ModuleType('subprocess')
    fake.CREATE_NO_WINDOW = 0
    fake.TimeoutExpired = type('TimeoutExpired', (Exception,), {})
    fake.run = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, 'subprocess', fake)
    with pytest.raises(ValueError, match='cwd'):
        wsa.run_powershell({'script': 'x', 'cwd': '/no/such/dir/xyz'})


def test_run_powershell_truncates_output(monkeypatch):
    fake = _fake_popen_module(('A' * 20000).encode())
    monkeypatch.setitem(sys.modules, 'subprocess', fake)
    out = wsa.run_powershell({'script': 'x'})
    assert len(out['stdout']) == wsa.OUTPUT_LIMIT and out['stdout_truncated'] is True


# ---------------- 截图压缩：坐标换算 + 体积下降 + 旁车恢复原始尺寸 ----------------

def test_compress_screenshot_scales_and_preserves_coordinates(tmp_path):
    from lib.windows_desktop_actions import compress_screenshot_file, image_point
    path = tmp_path / 'screen.png'
    Image.new('RGB', (2560, 1600), (30, 60, 90)).save(path)
    meta = compress_screenshot_file(path)
    assert meta['screen_size'] == [2560, 1600]
    assert max(meta['image_size']) <= 1600
    assert meta['bytes'] < meta['orig_bytes']   # 体积下降
    # 模型看到压缩图 (image_size)，点击坐标仍换算回原始屏幕
    monitor = {'width': 2560, 'height': 1600, 'left': 0, 'top': 0}
    args = {'screen_size': [2560, 1600], 'image_width': meta['image_size'][0],
            'image_height': meta['image_size'][1], 'x': meta['image_size'][0] / 2,
            'y': meta['image_size'][1] / 2}
    px, py = image_point(args, monitor)
    assert abs(px - 1280) <= 2 and abs(py - 800) <= 2


def test_desktop_request_recovers_screen_size_from_sidecar(tmp_path):
    from lib.windows_desktop import desktop_request
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    ref = uploads / 'shot.png'
    Image.new('RGB', (1600, 1000)).save(ref)          # 压缩图尺寸
    ref.with_name(ref.name + '.screen').write_text(json.dumps([2560, 1600]))
    args = {'action': 'click', 'reference_image': str(ref), 'x': 10, 'y': 10,
            'image_width': 1600, 'image_height': 1000}
    request = desktop_request(args, tmp_path)
    assert request['screen_size'] == [2560, 1600]       # 取旁车里的原始屏幕尺寸


# ---------------- 服务端校验：shell 开关、版本门禁、能力下发 ----------------

def _enroll_online(nodes, versions, alias='ops'):
    created = nodes.create(alias, 'https://litecode.example.com')
    code = re.search(r"-Code '([^']+)'", base64.b64decode(created['command'].split()[-1]).decode('utf-16le'))[1]
    device = nodes.redeem(code, {'computer': 'win11', 'user': 'tester'})
    nodes.claim(device['id'], device['token'], versions)   # heartbeat → online + 上报版本
    return device


def test_shell_disabled_rejects_run_powershell(tmp_path):
    from lib.windows_desktop import execute
    nodes = Nodes(tmp_path)
    _enroll_online(nodes, component_versions())           # 版本为最新
    with pytest.raises(ValueError, match='shell'):
        execute({'alias': 'ops', 'action': 'run_powershell', 'script': 'whoami'}, str(tmp_path))


def test_new_action_rejected_on_legacy_agent(tmp_path, monkeypatch):
    from lib import windows_desktop
    nodes = Nodes(tmp_path)
    _enroll_online(nodes, {'agent': '0' * 16, 'assets': '0' * 16})   # 版本落后 → updating, 等待超时后报错
    monkeypatch.setattr(windows_desktop, 'UPDATE_WAIT_SECONDS', 0)
    with pytest.raises(ValueError, match='节点正在更新'):
        windows_desktop.execute({'alias': 'ops', 'action': 'system_info'}, str(tmp_path))
    _enroll_online(nodes, {}, alias='old')                            # 不上报版本 = legacy, 立即拒绝
    with pytest.raises(ValueError, match='代理版本过低'):
        windows_desktop.execute({'alias': 'old', 'action': 'system_info'}, str(tmp_path))


def test_run_powershell_rejected_on_ssh_alias(tmp_path):
    from lib.windows_desktop import execute
    with pytest.raises(ValueError, match='主动回连'):
        execute({'alias': 'ssh-only', 'action': 'run_powershell', 'script': 'x'}, str(tmp_path))


def test_upload_source_must_be_in_workspace(tmp_path):
    from lib.windows_desktop import execute
    nodes = Nodes(tmp_path)
    _enroll_online(nodes, component_versions())
    outside = tmp_path.parent / 'outside.txt'
    outside.write_text('secret')
    with pytest.raises(ValueError, match='uploads'):
        execute({'alias': 'ops', 'action': 'upload', 'source': str(outside)}, str(tmp_path))


def test_upload_rejects_workspace_internal_file(tmp_path):
    """工作区根下的内部敏感文件 (凭据/会话) 不能被 upload 推到 Windows 节点。"""
    from lib.windows_desktop import execute
    nodes = Nodes(tmp_path)
    _enroll_online(nodes, component_versions())
    (tmp_path / '.secrets').mkdir(exist_ok=True)
    secret = tmp_path / '.secrets' / 'windows_credentials.json'
    secret.write_text('{"pw":"s3cr3t"}')
    with pytest.raises(ValueError, match='uploads'):
        execute({'alias': 'ops', 'action': 'upload', 'source': str(secret)}, str(tmp_path))


def test_set_capability_toggles_and_defaults_off(tmp_path):
    nodes = Nodes(tmp_path)
    device = _enroll_online(nodes, component_versions())
    assert nodes.node_capabilities(device['id'])['shell'] is False     # 默认关闭
    nodes.set_capability(device['id'], 'shell', True)
    assert nodes.node_capabilities(device['id'])['shell'] is True
    nodes.set_capability(device['id'], 'shell', False)
    assert nodes.node_capabilities(device['id'])['shell'] is False
    with pytest.raises(ValueError):
        nodes.set_capability(device['id'], 'bogus', True)


def test_tool_layer_cannot_enable_shell():
    # 模型不能自己开 shell：节点管理工具与系统工具的动作枚举里都没有开能力的入口。
    from lib.tools.defs.windows_nodes_defs import WINDOWS_NODES_DEFS
    from lib.tools.defs.windows_system_defs import WINDOWS_SYSTEM_DEFS
    node_actions = WINDOWS_NODES_DEFS[0]['function']['parameters']['properties']['action']['enum']
    sys_actions = WINDOWS_SYSTEM_DEFS[0]['function']['parameters']['properties']['action']['enum']
    # 没有任何"开启/设置能力"的动作入口（run_powershell 只是执行，不等于开权限）
    forbidden = ('enable', 'capab', 'set_shell', 'shell_on', 'grant')
    assert not any(any(f in a for f in forbidden) for a in node_actions + sys_actions)
    assert 'run_powershell' in sys_actions   # 执行入口在，但开关不在工具层


# ---------------- 旧代理兼容：本地二次校验 + 下发清单包含新文件 ----------------

def test_agent_run_job_double_checks_shell_switch(tmp_path):
    from lib import windows_node_agent as agent
    # 节点上 worker 是平铺模块；shell 校验在调用 run 之前，这里注入占位避免真导入。
    stub = types.ModuleType('windows_desktop_worker')
    stub.run = lambda *a, **k: {'ok': True}
    with patch.dict(sys.modules, {'windows_desktop_worker': stub}):
        job = {'id': 'job1', 'action': 'run_powershell', 'request': {'script': 'whoami'}}
        out = agent.run_job({'id': 'a' * 32}, tmp_path, job, capabilities={})
    assert out['result']['state'] == 'shell_disabled' and out['result']['ok'] is False
    assert not (tmp_path / 'jobs' / 'job1').exists()    # 任务目录已清理


def test_dispatch_manifest_includes_new_files():
    from lib import windows_node_agent as agent
    from routers import windows_nodes_router as router
    new = {'windows_system_actions.py', 'windows_file_actions.py', 'windows_transfer.py'}
    assert new <= set(WORKER_FILES)
    assert new <= set(agent.ASSETS)
    assert new <= set(router.ASSETS.values())
    assert {'windows_system_actions', 'windows_file_actions', 'windows_transfer'} <= set(agent.WORKER_MODULES)


def test_agent_and_server_hash_match_with_new_files(tmp_path):
    from lib import windows_node_agent as agent
    for asset, name in zip(agent.ASSETS, WORKER_FILES):
        (tmp_path / asset).write_bytes((ROOT / 'lib' / name).read_text().encode())
    assert agent.assets_version(tmp_path) == component_versions()['assets']


def test_loop_guard_gui_repeats_only_block_consecutive():
    """真机 2026-10-17: 回车/Alt+F4 在不同界面反复按被整轮累计误拦。GUI 工具只拦连续相同调用, 观察动作不计。"""
    from plugins.tools.guard import ToolLoopGuard
    g = ToolLoopGuard()
    enter = {"alias": "vm", "action": "hotkey", "keys": "enter"}
    click = lambda x: {"alias": "vm", "action": "click", "x": x, "y": 10}
    for i in range(6):                                   # 回车与不同点击交替 6 轮: 一次都不该拦
        assert g.check("windows_desktop", enter)[0] is False
        assert g.check("windows_desktop", click(i))[0] is False
    for _ in range(10):                                  # 反复截图等待: 不计
        assert g.check("windows_desktop", {"alias": "vm", "action": "screenshot"})[0] is False
    blocked = [g.check("windows_desktop", enter)[0] for _ in range(4)]
    assert blocked[:2] == [False, False] and blocked[-1] is True   # 连续相同仍会被拦
    # 非 GUI 工具保持原行为: 整轮累计
    g2 = ToolLoopGuard()
    res = [g2.check("web_fetch", {"url": "http://x"})[0] for _ in range(2)] + [g2.check("web_search", {"query": "y"})[0]]
    assert g2.check("web_fetch", {"url": "http://x"})[0] is True
