"""[2026-10-19] Windows 节点空转修复: 节点掉线提前结束等待 / 快捷键别名与白名单 / click_text 区分找不到与不唯一。"""
import base64
import io
import re
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib import windows_nodes
from lib.windows_nodes import Nodes, NodeGoneError


def online_node(tmp_path, alias='spin-node'):
    nodes = Nodes(tmp_path)
    created = nodes.create(alias, 'https://litecode.example.com')
    script = base64.b64decode(created['command'].split()[-1]).decode('utf-16le')
    code = re.search(r"-Code '([^']+)'", script)[1]
    device = nodes.redeem(code, {'computer': 'test', 'user': 'test'})
    task = nodes.claim(device['id'], device['token'])
    raw = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(raw, 'PNG')
    nodes.finish(device['id'], device['token'], task['id'], {'ok': True, 'state': 'desktop_ready', 'image': 'screen.png'},
                 {'screen.png': base64.b64encode(raw.getvalue()).decode()})
    return nodes, device


def age_node(nodes, alias, seconds):
    with nodes.locked() as data:
        data[alias]['last_seen'] = time.time() - seconds


# ---- 1. 节点掉线不再白等满 timeout ----

def test_wait_stops_early_when_node_heartbeat_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_nodes, 'NODE_GONE_CHECK_INTERVAL', 0)
    nodes, device = online_node(tmp_path)
    job = nodes.job('spin-node', 'screenshot', {})
    nodes.claim(device['id'], device['token'])
    age_node(nodes, 'spin-node', 60)
    started = time.monotonic()
    with pytest.raises(NodeGoneError, match='spin-node 已 6\\d 秒没心跳'):
        nodes.wait(job, 30)
    assert time.monotonic() - started < 5
    assert not (nodes.root / ('job-' + job + '.json')).exists()
    assert next(n for n in nodes.list() if n['alias'] == 'spin-node')['last_task']['state'] == 'unconfirmed'


def test_wait_keeps_waiting_while_heartbeat_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_nodes, 'NODE_GONE_CHECK_INTERVAL', 0)
    nodes, device = online_node(tmp_path)
    job = nodes.job('spin-node', 'screenshot', {})
    nodes.claim(device['id'], device['token'])
    age_node(nodes, 'spin-node', 30)   # 节流 + 任务心跳间隔内的正常静默, 不能误判离线
    with pytest.raises(TimeoutError):
        nodes.wait(job, 0.5)


def test_idempotent_job_not_retried_on_timeout_or_node_gone():
    from lib.windows_desktop import _run_node_job

    class FakeNodes:
        def __init__(self, exc):
            self.exc, self.jobs = exc, 0

        def job(self, *a):
            self.jobs += 1
            return 'x'

        def wait(self, *a):
            raise self.exc

    for exc in (TimeoutError('t'), NodeGoneError('g')):
        fake = FakeNodes(exc)
        with pytest.raises(type(exc)):
            _run_node_job(fake, 'a', 'screenshot', {}, 10, idempotent=True)
        assert fake.jobs == 1


# ---- 2. 快捷键 ----

@pytest.mark.parametrize('raw,expected', [
    ('Return', 'enter'), ('Esc', 'escape'), ('ESCAPE', 'escape'), ('shift', 'shift'), ('Ctrl+H', 'ctrl+h'),
    ('ctrl + shift + u', 'ctrl+shift+u'), ('shift+ctrl+u', 'ctrl+shift+u'), ('win+up', 'win+up'),
    ('Win+Down', 'win+down'), ('shift+home', 'shift+home'), ('Shift+End', 'shift+end'),
    ('control+c', 'ctrl+c'), ('ctrl++', 'ctrl+plus'), ('ctrl+-', 'ctrl+minus'), ('ctrl+shift+escape', 'ctrl+shift+esc'),
])
def test_hotkey_normalization(raw, expected):
    from lib.windows_desktop_actions import normalize_hotkey, HOTKEYS
    assert normalize_hotkey(raw) == expected and expected in HOTKEYS


@pytest.mark.parametrize('raw', ['win', 'win+l', 'win+s', 'win+x', 'ctrl+esc', 'shift+delete', ''])
def test_dangerous_hotkeys_stay_excluded(raw):
    from lib.windows_desktop_actions import normalize_hotkey
    with pytest.raises(ValueError):
        normalize_hotkey(raw)


def test_hotkey_events_cover_whole_whitelist():
    from lib.windows_desktop_actions import HOTKEYS, hotkey_events
    for keys in HOTKEYS.values():
        assert hotkey_events(keys)


def test_key_alias_param_and_server_side_normalization(tmp_path):
    from lib.windows_desktop import desktop_request
    assert desktop_request({'action': 'hotkey', 'key': 'Return'}, tmp_path)['keys'] == 'enter'
    assert desktop_request({'action': 'hotkey', 'keys': 'Ctrl+H'}, tmp_path)['keys'] == 'ctrl+h'
    with pytest.raises(ValueError, match='不支持的快捷键'):
        desktop_request({'action': 'hotkey', 'keys': 'win+l'}, tmp_path)


def test_run_dialog_guard_not_bypassed_by_spelling():
    from lib.windows_desktop_actions import shell_guard
    for spelling in ('R+Win', 'WIN + r', 'win+R'):
        with pytest.raises(ValueError, match='shell'):
            shell_guard('hotkey', {'key': spelling}, False)
    shell_guard('hotkey', {'keys': 'win+r'}, True)


# ---- 3. click_text ----

def ocr_line(text, words):
    """words: [(词文字, x)], 每词宽 10 高 10, y=50。"""
    return {'text': text, 'words': [{'text': w, 'bounding_rect': {'x': x, 'y': 50, 'width': 10, 'height': 10}}
                                    for w, x in words]}


def test_click_text_not_found_is_distinct_and_lists_ocr():
    from lib.windows_desktop_ocr import normalize_lines, select_text, TargetNotFound
    lines = normalize_lines({'lines': [ocr_line('0 仪 表 盘 》 云 游', [('0', 0), ('仪', 20), ('表', 40)])]}, keep_words=True)
    with pytest.raises(TargetNotFound, match='匹配 0 个.*仪 表 盘'):
        select_text(lines, '下 载', 1000, 1000)


def test_click_text_substring_with_cjk_spaces_clicks_matching_words():
    from lib.windows_desktop_ocr import normalize_lines, select_text
    raw = {'lines': [ocr_line('文 件 下 载 中 心', [('文', 0), ('件', 20), ('下', 40), ('载', 60), ('中', 80), ('心', 100)])]}
    lines = normalize_lines(raw, keep_words=True)
    target, x, y = select_text(lines, '下载', 1000, 1000)
    assert (x, y) == (55, 55)        # 只框 "下""载" 两个词, 不点整行中心 (55 恰为 40..70 中点)
    assert '_words' not in target
    target, x, _ = select_text(lines, '中心', 1000, 1000)
    assert x == 95


def test_click_text_exact_line_beats_substring_and_ambiguous_lists_candidates():
    from lib.windows_desktop_ocr import normalize_lines, select_text, TargetNotUnique
    lines = normalize_lines({'lines': [ocr_line('确 定', [('确', 0), ('定', 20)]),
                                       ocr_line('确定删除吗', [('确定删除吗', 200)])]}, keep_words=True)
    _, x, _ = select_text(lines, '确定', 1000, 1000)
    assert x == 15
    lines = normalize_lines({'lines': [ocr_line('保存草稿', [('保存草稿', 0)]),
                                       ocr_line('另 保存', [('另', 100), ('保存', 120)])]}, keep_words=True)
    with pytest.raises(TargetNotUnique) as info:
        select_text(lines, '保存', 1000, 1000)
    assert info.value.candidates == ['保存草稿', '另 保存']


def test_perform_reports_not_found_vs_not_unique(tmp_path, monkeypatch):
    import ctypes
    import types
    from unittest.mock import Mock, patch
    from lib import windows_desktop_ocr as ocr
    mouse = types.SimpleNamespace(click=Mock(), FAILSAFE=True)
    monkeypatch.setitem(sys.modules, 'pyautogui', mouse)
    monkeypatch.setattr(ctypes, 'windll', types.SimpleNamespace(
        user32=types.SimpleNamespace(SetProcessDPIAware=Mock())), raising=False)
    frame = {'lines': [{'text': '保存', 'rect': {'x': 0, 'y': 0, 'width': 10, 'height': 10}, '_words': []},
                       {'text': '保存', 'rect': {'x': 50, 'y': 0, 'width': 10, 'height': 10}, '_words': []}],
             'width': 100, 'height': 100, 'origin': [0, 0]}
    with patch.object(ocr, 'capture_ocr', return_value=frame):
        missing = ocr.perform('click_text', tmp_path, {'text': '下载'})
        dup = ocr.perform('click_text', tmp_path, {'text': '保存'})
    assert missing['state'] == 'target_not_found' and dup['state'] == 'target_not_unique'
    assert dup['candidates'] == ['保存']
    assert all('_words' not in line for line in dup['lines'])
    mouse.click.assert_not_called()
