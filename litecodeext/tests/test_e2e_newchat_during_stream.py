"""长任务进行中点「新对话」不卡 (BACKLOG Inbox 2026-10-23) + GET /api/sessions 摘要缓存。

- 前端静态检查: newChat 不能再因 streaming 直接 return, 要先断开当前订阅 (与 swS 共用 _detachStream)
- list_sessions 摘要缓存: 返回字段不变; 文件改动后摘要跟着变; 无用户消息的 server 会话不再反复整读
- 完整浏览器 E2E (起隔离测试实例 + playwright, 约 1 分钟): 默认 skip,
  LITECODE_E2E_NEWCHAT=1 时运行, 等价于 python3 scripts/e2e_newchat_during_stream.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
ASSETS = ROOT / 'web_assets'


def test_newchat_not_blocked_by_streaming():
    js = (ASSETS / 'chat_render.js').read_text()
    body = js[js.index('async function newChat()'):]
    body = body[:body.index('\n}\n')]
    assert 'if(streaming)return' not in body.replace(' ', '')
    assert '_detachStream()' in body and "localStorage.setItem('lc.cur'" in body
    assert 'function _detachStream()' in js and js.count('_detachStream();') >= 2   # swS + newChat
    pg = (ASSETS / 'project_groups.js').read_text()
    fn = pg[pg.index('async function newChatIn'):]
    assert 'window.streaming' not in fn[:fn.index('\n  }\n')]
    st = (ASSETS / 'chat_stream.js').read_text()
    assert 'if(gen!==_streamGen){try{resp.body&&resp.body.cancel()' in st


@pytest.fixture
def wui(tmp_path, monkeypatch):
    import web_ui
    web_dir, srv_dir = tmp_path / 'web', tmp_path / 'srv'
    web_dir.mkdir()
    srv_dir.mkdir()
    monkeypatch.setattr(web_ui, 'SESSIONS_DIR', web_dir)
    monkeypatch.setitem(web_ui.CFG, 'paths', {'workspace_base': str(tmp_path), 'sessions_dir': str(srv_dir)})
    monkeypatch.setattr(web_ui, '_load_tombstones', lambda: set())
    web_ui._LIST_META.clear()
    web_ui._t = (web_dir, srv_dir)
    yield web_ui
    web_ui._LIST_META.clear()


def test_list_sessions_cache_fields_and_invalidation(wui, monkeypatch):
    web_dir, srv_dir = wui._t
    for i in range(3):
        d = {'id': f'web-{i}', 'name': f'n{i}', 'created': 10 + i, 'last_used': 20 + i, 'owner': 'anon',
             'messages': [{'role': 'user', 'content': 'x'}] * (i + 1)}
        (web_dir / f'web-{i}.json').write_text(json.dumps(d))
    (srv_dir / 'cli-empty.json').write_text(json.dumps({'ts': 1, 'msgs': [{'role': 'system', 'content': 's'}]}))
    first = wui.list_sessions()
    assert [s['id'] for s in first] == ['web-2', 'web-1', 'web-0']
    assert first[0] == {'id': 'web-2', 'name': 'n2', 'created': 12, 'last_used': 22, 'count': 3}

    reads = []
    orig = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **k: (reads.append(self.name), orig(self, *a, **k))[1])
    assert wui.list_sessions() == first
    assert reads == []          # 文件没变: 一个都不重读 (含无用户消息的 server 会话)

    f = web_dir / 'web-0.json'
    d = json.loads(orig(f))
    d['messages'].append({'role': 'assistant', 'content': 'y'})
    d['last_used'] = 99
    f.write_text(json.dumps(d))
    os.utime(f, ns=(time.time_ns(), time.time_ns() + 1_000_000))
    again = wui.list_sessions()
    assert again[0] == {'id': 'web-0', 'name': 'n0', 'created': 10, 'last_used': 99, 'count': 2}
    again[0]['count'] = -1      # 调用方改返回值不能污染缓存
    assert wui.list_sessions()[0]['count'] == 2


@pytest.mark.skipif(os.environ.get('LITECODE_E2E_NEWCHAT') != '1', reason='浏览器 E2E 约 1 分钟, LITECODE_E2E_NEWCHAT=1 时运行')
def test_newchat_during_long_task_browser():
    pytest.importorskip('playwright.sync_api')
    script = ROOT.parent / 'scripts' / 'e2e_newchat_during_stream.py'
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
