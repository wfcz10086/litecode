"""远程操控闭环 (2026-10-21 真机实测后的修复):
- 窗口标题含零宽字符也能精确定位, 匹配失败给出近似标题;
- launch_app / open_url 等新窗口出现并切到前台, 切不过去如实报 focus_failed;
- 未设默认浏览器时 open_url 直接起 Edge/Chrome, 不弹"选择应用"对话框;
- 动作后等界面稳定再截图 (wait_settled), 不再固定 sleep;
- UI Automation 控件树 ui_tree / click_element;
- OCR 小屏先放大 2 倍;
- 文件传输上限 1GB, 两端流式读写/算哈希, 节点忙时排队而不是直接失败;
- 远程桌面断开与锁屏分开报告。
"""
import base64
import hashlib
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core'), str(ROOT / 'lib')]

from lib import windows_desktop_actions as actions   # noqa: E402


# ---------- 窗口标题 ----------

def test_choose_window_ignores_zero_width_and_spacing():
    windows = [{'title': 'Example Domain - 用户配置 1 - Microsoft\u200b Edge', 'handle': 7}]
    assert actions.choose_window(windows, 'Example Domain - 用户配置 1 - Microsoft Edge')['handle'] == 7
    assert actions.choose_window(windows, 'Example  Domain - 用户配置 1 - Microsoft Edge ')['handle'] == 7


def test_choose_window_still_requires_full_title_but_hints_candidates():
    windows = [{'title': 'notepad_zh.txt - Notepad', 'handle': 1}, {'title': 'LiteCode', 'handle': 2}]
    with pytest.raises(ValueError) as err:
        actions.choose_window(windows, 'Notepad')
    assert 'notepad_zh.txt - Notepad' in str(err.value) and '匹配 0 个' in str(err.value)


# ---------- 新窗口跟随 ----------

def test_pick_new_window_prefers_process_hint_and_skips_input_host():
    before = {1}
    windows = [{'handle': 1, 'title': 'LiteCode', 'process': 'msedge.exe'},
               {'handle': 5, 'title': 'Windows 输入体验', 'process': 'TextInputHost.exe'},
               {'handle': 6, 'title': '帮助', 'process': 'other.exe'},
               {'handle': 9, 'title': 'Untitled - Notepad', 'process': 'Notepad.exe'}]
    assert actions.pick_new_window(before, windows, 'notepad')['handle'] == 9
    assert actions.pick_new_window(before, windows, '')['handle'] == 6
    assert actions.pick_new_window({1, 5, 6, 9}, windows, 'notepad') is None


def test_follow_new_window_focuses_new_window(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, 'bring_to_front', lambda handle: calls.append(handle) or True)
    monkeypatch.setattr(actions.time, 'sleep', lambda s: None)
    frames = iter([[{'handle': 1, 'title': 'LiteCode', 'process': 'msedge.exe'}],
                   [{'handle': 1, 'title': 'LiteCode', 'process': 'msedge.exe'},
                    {'handle': 3, 'title': 'Untitled - Notepad', 'process': 'Notepad.exe'}]])
    result = actions.follow_new_window({1}, 'notepad', wait=5, lister=lambda: next(frames))
    assert result['focused'] is True and result['new_window']['handle'] == 3 and calls == [3]


def test_follow_new_window_reports_missing_window(monkeypatch):
    monkeypatch.setattr(actions, 'bring_to_front', lambda handle: pytest.fail('没有新窗口不该切换'))
    monkeypatch.setattr(actions.time, 'sleep', lambda s: None)
    clock = iter(range(100))
    monkeypatch.setattr(actions.time, 'monotonic', lambda: next(clock))
    result = actions.follow_new_window({1}, 'notepad', wait=3,
                                       lister=lambda: [{'handle': 1, 'title': 'x', 'process': 'a.exe'}])
    assert result['focused'] is False and result['new_window'] is None and 'focus_window' in result['warning']


# ---------- open_url ----------

def test_open_url_without_default_browser_launches_browser_exe(monkeypatch):
    launched = []
    monkeypatch.setattr(actions, 'has_default_browser', lambda: False)
    monkeypatch.setattr(actions, '_app_path', lambda exe: r'C:\Edge\msedge.exe' if exe == 'msedge.exe' else None)
    monkeypatch.setattr(actions, 'detached_popen', lambda cmd, cwd=None: launched.append(cmd))
    monkeypatch.setitem(sys.modules, 'webbrowser', types.SimpleNamespace(open=lambda u: pytest.fail('不应走系统关联')))
    assert actions.open_url('https://example.com/') == ('msedge', 'msedge.exe')
    assert launched == [[r'C:\Edge\msedge.exe', 'https://example.com/']]


def test_open_url_with_default_browser_uses_association(monkeypatch):
    opened = []
    monkeypatch.setattr(actions, 'has_default_browser', lambda: True)
    monkeypatch.setattr(actions, 'detached_popen', lambda *a, **k: pytest.fail('有默认浏览器时不直接起程序'))
    monkeypatch.setitem(sys.modules, 'webbrowser', types.SimpleNamespace(open=lambda u: opened.append(u) or True))
    monkeypatch.setattr(actions.time, 'sleep', lambda s: None)
    monkeypatch.setattr(actions, 'openwith_windows', lambda: [])
    assert actions.open_url('https://example.com/') == ('default', '')
    assert opened == ['https://example.com/']


def test_open_url_closes_openwith_chooser_and_launches_browser(monkeypatch):
    launched, closed = [], []
    monkeypatch.setattr(actions, 'has_default_browser', lambda: True)
    monkeypatch.setitem(sys.modules, 'webbrowser', types.SimpleNamespace(open=lambda u: True))
    monkeypatch.setattr(actions.time, 'sleep', lambda s: None)
    monkeypatch.setattr(actions, 'openwith_windows', lambda: [42])
    monkeypatch.setattr(actions, '_app_path', lambda exe: r'C:\Chrome\chrome.exe' if exe == 'chrome.exe' else None)
    monkeypatch.setattr(actions, 'detached_popen', lambda cmd, cwd=None: launched.append(cmd))
    user32 = types.SimpleNamespace(PostMessageW=lambda h, msg, w, l: closed.append((h.value, msg)) or 1)
    monkeypatch.setattr(actions.ctypes, 'windll', types.SimpleNamespace(user32=user32), raising=False)
    method, exe = actions.open_url('https://example.com/')
    assert method.startswith('chrome') and exe == 'chrome.exe'
    assert closed == [(42, 0x0010)] and launched == [[r'C:\Chrome\chrome.exe', 'https://example.com/']]


def test_open_url_rejects_unknown_browser_and_bad_url():
    with pytest.raises(ValueError):
        actions.open_url('https://example.com/', 'ie6')
    with pytest.raises(ValueError):
        actions.open_url('file:///C:/Windows/win.ini')


# ---------- 稳定等待 ----------

class _Frame:
    def __init__(self, value):
        self.value = bytes([value]) * 100

    def tobytes(self):
        return self.value


def _fake_clock(step=0.3):
    now = [0.0]

    def clock():
        now[0] += step
        return now[0]
    return clock


def test_frame_delta_and_wait_settled_returns_when_stable(monkeypatch):
    monkeypatch.setattr(actions.time, 'sleep', lambda s: None)
    assert actions.frame_delta(_Frame(10), _Frame(10)) == 0
    assert actions.frame_delta(_Frame(10), _Frame(30)) == 20
    frames = iter([_Frame(0), _Frame(80), _Frame(120), _Frame(120), _Frame(120)])
    settled, waited = actions.wait_settled(max_wait=10, grab=lambda: next(frames), clock=_fake_clock())
    assert settled is True and waited > 0
    # 只稳了一个间隔又变了 (对话框延迟关闭) 不算稳定, 继续等
    seq = [_Frame(0), _Frame(0), _Frame(90), _Frame(90), _Frame(90)]
    grabbed = []
    frames = iter(seq)
    settled, _ = actions.wait_settled(max_wait=10, grab=lambda: grabbed.append(1) or next(frames), clock=_fake_clock())
    assert settled is True and len(grabbed) == 5


def test_wait_settled_gives_up_after_max_wait(monkeypatch):
    monkeypatch.setattr(actions.time, 'sleep', lambda s: None)
    counter = iter(range(0, 10000, 37))
    settled, _ = actions.wait_settled(max_wait=2, grab=lambda: _Frame(next(counter) % 256), clock=_fake_clock())
    assert settled is False


# ---------- UI Automation ----------

class _Rect:
    def __init__(self, x, y, w, h):
        self.left, self.top, self.right, self.bottom = x, y, x + w, y + h


class _Ctrl:
    def __init__(self, kind, name, rect, children=(), value=None, enabled=True):
        self.ControlTypeName, self.Name, self.BoundingRectangle = kind + 'Control', name, _Rect(*rect)
        self._children, self._value, self.IsEnabled = list(children), value, enabled

    def GetChildren(self):
        return self._children

    def GetValuePattern(self):
        if self._value is None:
            raise RuntimeError('no value pattern')
        return types.SimpleNamespace(Value=self._value)


def _tree():
    return _Ctrl('Window', 'lc_remote_test - 文件资源管理器', (250, 86, 800, 600), [
        _Ctrl('Button', '后退', (269, 134, 32, 32), [_Ctrl('Text', '\ue72b', (279, 144, 12, 12))]),
        _Ctrl('Edit', '地址栏', (459, 134, 406, 32), value='C:\\Users\\litecode\\Documents'),
        _Ctrl('List', '项目视图', (418, 222, 347, 430), [
            _Ctrl('ListItem', 'notepad_zh.txt', (432, 255, 983, 24)),     # 超出窗口右边, 要裁剪
            _Ctrl('ListItem', 'hidden.txt', (432, 2000, 300, 24)),        # 滚出可见区, 不返回
        ]),
        _Ctrl('Button', '保存(S)', (500, 600, 80, 30), enabled=False),
        _Ctrl('Button', '新建\u200b\u200b文件夹\u200b', (600, 600, 80, 30)),     # 真机名称夹零宽字符
        _Ctrl('Pane', '', (250, 86, 800, 600)),                          # 无名且不可交互, 跳过
    ])


def test_collect_elements_clips_filters_and_reads_values():
    items, truncated, visited = actions.collect_elements(_tree(), [250, 86, 800, 600])
    names = [(i['type'], i['name']) for i in items]
    assert ('ListItem', 'notepad_zh.txt') in names and ('ListItem', 'hidden.txt') not in names
    assert ('Text', '') not in names                                     # 私用区图标字符被剔除后无名, 且非交互
    item = next(i for i in items if i['name'] == 'notepad_zh.txt')
    assert item['rect'] == [432, 255, 618, 24] and item['center'] == [741, 267]
    edit = next(i for i in items if i['type'] == 'Edit')
    assert edit['value'].endswith('Documents')
    assert next(i for i in items if i['name'] == '保存(S)')['enabled'] is False
    assert ('Button', '新建文件夹') in names
    assert truncated is False and visited == 10


def test_collect_elements_text_and_type_filters_and_limit():
    items, _, _ = actions.collect_elements(_tree(), [250, 86, 800, 600], text='NOTEPAD')
    assert [i['name'] for i in items] == ['notepad_zh.txt']
    items, _, _ = actions.collect_elements(_tree(), [250, 86, 800, 600], control_type='ButtonControl')
    assert {i['name'] for i in items} == {'后退', '保存(S)', '新建文件夹'}
    items, truncated, _ = actions.collect_elements(_tree(), [250, 86, 800, 600], limit=2)
    assert len(items) == 2 and truncated is True


def test_choose_element_exact_then_unique_substring_and_ambiguity():
    items = [{'type': 'Button', 'name': '保存(S)', 'center': [1, 1]},
             {'type': 'Button', 'name': '取消', 'center': [2, 2]},
             {'type': 'ListItem', 'name': 'a.txt', 'center': [3, 3]},
             {'type': 'ListItem', 'name': 'b.txt', 'center': [4, 4]},
             {'type': 'Edit', 'name': '文件名:', 'value': 'note.txt', 'center': [5, 5]}]
    assert actions.choose_element(items, '保存')['center'] == [1, 1]
    assert actions.choose_element(items, ' 取 消 ')['center'] == [2, 2]
    assert actions.choose_element(items, 'note.txt')['type'] == 'Edit'          # 输入框按当前值也能定位
    with pytest.raises(ValueError, match='匹配 2 个'):
        actions.choose_element(items, '.txt')
    with pytest.raises(ValueError, match='匹配 0 个'):
        actions.choose_element(items, '确定')


def test_choose_element_prefers_enabled_controls():
    items = [{'type': 'Button', 'name': '下一步', 'center': [1, 1], 'enabled': False},
             {'type': 'Button', 'name': '下一步', 'center': [9, 9]}]
    assert actions.choose_element(items, '下一步')['center'] == [9, 9]


# ---------- OCR 放大 ----------

def test_ocr_scale_upscales_small_screens_only():
    from lib.windows_desktop_ocr import ocr_scale
    assert ocr_scale((1280, 800), 10000) == 0.5
    assert ocr_scale((2560, 1440), 10000) == 1
    assert ocr_scale((12000, 3000), 10000) == 1.2
    assert ocr_scale((1280, 800), 2000) == 1          # 放大后会超过引擎上限时不放大


def test_normalize_lines_maps_upscaled_coordinates_back():
    from lib.windows_desktop_ocr import normalize_lines
    result = {'lines': [{'text': '新建', 'words': [{'text': '新建', 'bounding_rect': {'x': 200, 'y': 100, 'width': 40, 'height': 20}}]}]}
    assert normalize_lines(result, 0.5)[0]['rect'] == {'x': 100, 'y': 50, 'width': 20, 'height': 10}


# ---------- 传输 ----------

def test_transfer_limit_is_1gb_and_streaming_helpers(tmp_path):
    from lib import windows_transfer as wt
    assert wt.MAX_TRANSFER == 1024 ** 3
    assert wt.chunk_count(60 * 1024 * 1024) == 15
    with pytest.raises(ValueError):
        wt.chunk_count(wt.MAX_TRANSFER + 1)
    data = bytes(range(256)) * 50000
    path = tmp_path / 'f.bin'
    path.write_bytes(data)
    assert wt.sha256_file(path) == hashlib.sha256(data).hexdigest()
    assert wt.read_chunk(path, 0) == data[:wt.CHUNK]
    with pytest.raises(ValueError):
        wt.read_chunk(path, 99)


class _FakeNodes:
    """内存里模拟节点: upload 逐块写入, download 按块读; busy 次数内 job() 报节点忙。"""

    def __init__(self, store, busy=0):
        self.store, self.busy, self.jobs, self.results = store, busy, [], {}

    def job(self, alias, action, request, timeout=60):
        if self.busy:
            self.busy -= 1
            raise ValueError('该节点已有桌面任务，等完成后再操作')
        from lib import windows_file_actions as fa
        job_id = str(len(self.jobs))
        self.jobs.append(action)
        with patch.object(fa, 'downloads_dir', lambda: self.store):
            self.results[job_id] = fa.perform(action, None, request)
        return job_id

    def wait(self, job_id, timeout):
        return self.results.pop(job_id)


def test_node_upload_and_download_roundtrip_streaming(tmp_path, monkeypatch):
    from lib import windows_desktop as wd
    from lib import windows_transfer as wt
    from lib import windows_file_actions as fa
    monkeypatch.setattr(wt, 'CHUNK', 64 * 1024)
    monkeypatch.setattr(fa.wt, 'CHUNK', 64 * 1024)          # 节点端模块可能按顶层名导入了另一份
    workspace = tmp_path / 'ws'
    (workspace / 'uploads').mkdir(parents=True)
    store = tmp_path / 'win'
    store.mkdir()
    data = hashlib.sha256(b'seed').digest() * 10000          # 320000 字节, 5 块
    (workspace / 'uploads' / 'big.bin').write_bytes(data)
    nodes = _FakeNodes(store)
    up = wd._node_upload(nodes, 'win', {'source': 'big.bin'}, workspace, 30)
    assert up['ok'] and up['chunks'] == 5 and up['sha256'] == hashlib.sha256(data).hexdigest()
    assert (store / 'big.bin').read_bytes() == data and 'mb_per_s' in up
    down = wd._node_download(nodes, 'win', {'path': str(store / 'big.bin')}, workspace, 30)
    assert Path(down['path']).read_bytes() == data and down['size'] == len(data)
    assert not list((workspace / 'uploads').glob('.part-*'))


def test_node_download_hash_mismatch_leaves_no_partial_file(tmp_path, monkeypatch):
    from lib import windows_desktop as wd
    workspace = tmp_path / 'ws'
    (workspace / 'uploads').mkdir(parents=True)
    replies = iter([{'ok': True, 'name': 'x.bin', 'chunks': 1, 'sha256': '0' * 64},
                    {'ok': True, 'chunk': base64.b64encode(b'abc').decode()}])
    monkeypatch.setattr(wd, '_run_node_job', lambda *a, **k: next(replies))
    with pytest.raises(ValueError, match='sha256'):
        wd._node_download(object(), 'win', {'path': 'C:/x.bin'}, workspace, 30)
    assert list((workspace / 'uploads').iterdir()) == []


def test_busy_node_queues_instead_of_failing(tmp_path):
    from lib import windows_desktop as wd
    nodes = _FakeNodes(tmp_path, busy=2)
    sleeps = []
    job_id = wd._dispatch_when_free(nodes, 'win', 'list_dir', {'path': str(tmp_path)}, 30, wait=10, sleep=sleeps.append)
    assert nodes.wait(job_id, 30)['ok'] is True and len(sleeps) == 2
    nodes.busy = 5
    with pytest.raises(ValueError, match='已有桌面任务'):
        wd._dispatch_when_free(nodes, 'win', 'list_dir', {'path': str(tmp_path)}, 30, wait=0, sleep=lambda s: None)


# ---------- 服务端请求 / 工具定义 / 路由 ----------

def test_desktop_request_passes_uia_and_browser_fields(tmp_path):
    from lib.windows_desktop import desktop_request, NEW_ACTIONS, IDEMPOTENT_ACTIONS
    request = desktop_request({'action': 'click_element', 'text': '保存', 'control_type': 'Button'}, str(tmp_path))
    assert request['text'] == '保存' and request['control_type'] == 'Button'
    assert desktop_request({'action': 'open_url', 'url': 'https://a.b', 'browser': 'chrome'}, str(tmp_path))['browser'] == 'chrome'
    with pytest.raises(ValueError, match='click_element'):
        desktop_request({'action': 'click_element'}, str(tmp_path))
    assert {'ui_tree', 'click_element'} <= set(NEW_ACTIONS) and 'ui_tree' in IDEMPOTENT_ACTIONS


def test_tool_definition_advertises_closed_loop_actions():
    from lib.tools.defs.windows_desktop_defs import WINDOWS_DESKTOP_DEFS
    fn = WINDOWS_DESKTOP_DEFS[0]['function']
    props = fn['parameters']['properties']
    assert {'ui_tree', 'click_element'} <= set(props['action']['enum'])
    assert props['browser']['enum'] == ['msedge', 'chrome', 'firefox'] and 'control_type' in props
    assert 'ui_tree' in fn['description'] and 'focus_failed' in fn['description']


@pytest.mark.parametrize('action', ('ui_tree', 'click_element'))
def test_worker_routes_uia_actions_to_desktop_module(tmp_path, action):
    from lib import windows_desktop_worker as worker
    calls = []
    backend = types.SimpleNamespace(perform=lambda a, f, args: calls.append(a) or {'ok': True, 'state': a})
    with patch.object(worker, 'desktop_ready', return_value=True), \
         patch.dict(sys.modules, {'windows_desktop_actions': backend}):
        assert worker.run(action, tmp_path, {'text': 'x'})['ok'] is True
    assert calls == [action]


def test_disconnected_session_is_reported_separately(tmp_path):
    from lib import windows_desktop_worker as worker
    with patch.object(worker, 'desktop_ready', return_value=False), \
         patch.object(worker, 'session_state', return_value=('disconnected', 2)):
        result = worker.run('screenshot', tmp_path, {})
    assert result['state'] == 'desktop_unavailable' and result['session_state'] == 'disconnected'
    assert 'tscon 2 /dest:console' in result['error']
    with patch.object(worker, 'desktop_ready', return_value=False), \
         patch.object(worker, 'session_state', return_value=('active', 1)):
        assert '解锁' in worker.run('screenshot', tmp_path, {})['error']


def test_readback_gate_treats_click_element_save_as_file_change():
    from lib.agent.readback_gate import is_file_change
    assert is_file_change('click_element', {'text': '保存(S)'}) is True
    assert is_file_change('click_element', {'text': '确定'}) is False


def test_page_content_seen_detects_empty_chromium_document():
    shell = [{'type': 'Button', 'name': '返回', 'rect': [0, 0, 10, 10], 'center': [5, 5]},
             {'type': 'Document', 'name': '', 'rect': [0, 100, 500, 500], 'center': [250, 350]}]
    assert actions.page_content_seen(shell) is False
    assert actions.page_content_seen(shell + [{'type': 'Text', 'name': 'Example', 'rect': [10, 200, 50, 10],
                                               'center': [35, 205]}]) is True
    assert actions.page_content_seen(shell[:1]) is True       # 不是网页窗口, 不重试


def test_choose_element_merges_nested_duplicates():
    outer = {'type': 'Edit', 'name': '地址栏', 'rect': [459, 134, 406, 32], 'center': [662, 150]}
    inner = dict(outer, value='C:\\Users')
    assert actions.choose_element([outer, inner], '地址栏') is inner


def test_choose_element_single_symbol_needs_exact_name_and_lists_clickables():
    items = [{'type': 'Button', 'name': 'Bold (Ctrl+B)', 'rect': [1, 1, 5, 5], 'center': [3, 3]},
             {'type': 'Button', 'name': 'Italic (Ctrl+I)', 'rect': [9, 1, 5, 5], 'center': [11, 3]},
             {'type': 'Button', 'name': 'Add New Tab', 'rect': [20, 1, 5, 5], 'center': [22, 3]}]
    with pytest.raises(ValueError) as err:
        actions.choose_element(items, '+')
    assert '匹配 0 个' in str(err.value) and 'Add New Tab' in str(err.value)


def test_pick_new_window_prefers_main_window_over_popup():
    windows = [{'handle': 2, 'title': '翻译 俄语 页面?', 'process': 'msedge.exe', 'rect': [330, 70, 320, 180]},
               {'handle': 3, 'title': 'Example Domain - Microsoft Edge', 'process': 'msedge.exe', 'rect': [10, 0, 1050, 752]}]
    assert actions.pick_new_window({1}, windows, 'msedge')['handle'] == 3


def test_compact_elements_and_windows_keep_results_small():
    primary = {'left': 0, 'top': 0, 'width': 1280, 'height': 800}
    items = [{'type': 'Button', 'name': 'File', 'rect': [62, 94, 41, 32], 'center': [82, 110]},
             {'type': 'Text', 'name': 'File', 'rect': [72, 101, 21, 19], 'center': [82, 110]},
             {'type': 'Edit', 'name': '地址栏', 'rect': [1400, 10, 100, 20], 'center': [1450, 20], 'value': 'C:'}]
    out = actions.compact_elements(items, primary)
    assert [e['name'] for e in out] == ['File', '地址栏'] and 'rect' not in out[0] and out[0]['size'] == [41, 32]
    assert out[1]['offscreen'] is True and out[1]['value'] == 'C:'
    wins = actions.compact_windows([{'handle': 1, 'title': 'a', 'process': 'p', 'foreground': True,
                                     'minimized': False, 'rect': [0, 0, 1, 1]}])
    assert wins == [{'handle': 1, 'title': 'a', 'process': 'p', 'foreground': True}]


def test_unknown_action_aliases_are_normalized(tmp_path):
    from lib import windows_desktop as wd
    seen = []

    class Nodes:
        def __init__(self, workspace):
            pass

        def list(self):
            return [{'alias': 'w', 'update_state': 'current', 'capabilities': {'shell': True}}]

    def run(nodes, alias, action, request, timeout, idempotent=False):
        seen.append((action, request.get('keys')))
        return {'ok': True}
    with patch('lib.windows_nodes.Nodes', Nodes), patch.object(wd, '_run_node_job', run):
        result = wd.execute({'alias': 'w', 'action': 'press', 'keys': 'Return'}, str(tmp_path))
    assert seen == [('hotkey', 'enter')] and result['action'] == 'hotkey'
    with pytest.raises(ValueError, match='不支持'):
        wd.execute({'alias': 'w', 'action': 'format_disk'}, str(tmp_path))


def test_upload_source_accepts_uploads_prefix(tmp_path):
    from lib.windows_transfer import resolve_upload_source
    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'uploads' / 'a.bin').write_bytes(b'x')
    expected = (tmp_path / 'uploads' / 'a.bin').resolve()
    assert resolve_upload_source(tmp_path, 'uploads/a.bin') == expected
    assert resolve_upload_source(tmp_path, 'a.bin') == expected
    with pytest.raises(ValueError):
        resolve_upload_source(tmp_path, 'uploads/../.secrets/x.json')


def test_busy_node_error_names_the_running_job(tmp_path):
    from tests.test_windows_nodes import enroll, complete_initial
    from lib.windows_nodes import Nodes
    nodes = Nodes(tmp_path)
    _, _, device = enroll(nodes, 'busy')
    complete_initial(nodes, device)
    nodes.job('busy', 'run_powershell', {'script': 'Start-Sleep 30'}, timeout=120)
    with pytest.raises(ValueError) as err:
        nodes.job('busy', 'screenshot', {})
    message = str(err.value)
    assert '已有桌面任务' in message and 'run_powershell' in message and '不要自己 sleep 轮询' in message
