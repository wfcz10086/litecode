"""Web 后台子代理面板接口 (routers/subagents_router.py → gateway/subagents.py → subagent_registry)。

整条链路在进程内跑: web 侧 router 经 S.aproxy 转发, 这里把 aproxy 换成打到 gateway app 的
httpx ASGITransport (失败形态与 web_ui._aproxy 一致: <400 返回 json, 否则 {"error","status"})。
覆盖: 列表、跨会话隔离 (A 看不到/控不了 B)、stop、send (含长度上限与 agent_control 一致)、
result 只读不标"已取走"、未登录 401、会话属于别的用户 403、gateway 侧无 Bearer 401;
另对 web_assets/subagent_panel.js 做 node --check 与禁用弹窗的静态检查。
"""
import asyncio
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib.agent import subagent_registry as reg  # noqa: E402

TOKEN = 'gw-test-token'


class FakeWebUI:
    def __init__(self, owners):
        self.owners = owners

    def load_session(self, sid):
        d = {'id': sid, 'name': sid, 'created': 1, 'last_used': 1, 'messages': []}
        if sid in self.owners:
            d['owner'] = self.owners[sid]
        return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    old_over, old_restored = reg.WORKSPACE_OVERRIDE, reg._RESTORED
    reg._RECORDS.clear()
    reg.WORKSPACE_OVERRIDE = tmp_path
    reg._RESTORED = True

    from gateway import _deps, subagents as gw_sub
    from routers import subagents_router as sr
    from routers.state import S
    import plugins.auth.core as auth
    monkeypatch.setattr(_deps, '_srv', lambda: SimpleNamespace(TOKEN=TOKEN))
    gw = FastAPI()
    gw.include_router(gw_sub.router)
    upstream_calls = []

    async def aproxy(path, method='GET', **kw):
        upstream_calls.append((method, path))
        kw.pop('timeout', None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw), base_url='http://srv') as c:
            r = await c.request(method, path, headers={'Authorization': f'Bearer {TOKEN}'}, **kw)
        return r.json() if r.status_code < 400 else {'error': f'HTTP {r.status_code}', 'status': r.status_code}

    monkeypatch.setattr(S, 'aproxy', aproxy, raising=False)
    wui = FakeWebUI({'A': 'anon', 'B': 'anon', 'X': 'bob'})
    monkeypatch.setattr(sr, '_wui', lambda: wui)
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'good': time.time() + 3600})
    app = FastAPI()
    app.include_router(sr.router)
    client = TestClient(app)
    client.cookies.set('lc_auth', 'good')
    yield SimpleNamespace(c=client, gw=gw, calls=upstream_calls, ws=tmp_path)
    reg._RECORDS.clear()
    reg.WORKSPACE_OVERRIDE = old_over
    reg._RESTORED = old_restored


def _mk(sid, task='调研 X 模块', agent_type='researcher', background=True):
    return reg._new_record(sid, agent_type, task, background=background)


def test_list_shows_only_own_session(env):
    a1 = _mk('A', task='任务 A1')
    a2 = _mk('A', task='任务 A2', agent_type='coder')
    reg._finish(a2, 'done', '结论 A2')
    b1 = _mk('B', task='任务 B1')
    r = env.c.get('/api/sessions/A/subagents')
    assert r.status_code == 200
    d = r.json()
    ids = [i['id'] for i in d['items']]
    assert ids == [a1.id, a2.id] and b1.id not in ids
    assert d['running'] == 1 and d['send_max'] == reg.SEND_MAX
    it = d['items'][0]
    for k in ('id', 'agent_type', 'brief', 'status', 'status_cn', 'elapsed', 'iteration', 'last_tool', 'progress'):
        assert k in it
    assert 'result' not in it and 'sid' not in it and it['status'] == 'running'
    rb = env.c.get('/api/sessions/B/subagents').json()
    assert [i['id'] for i in rb['items']] == [b1.id]
    assert env.c.get('/api/sessions/empty/subagents').json()['items'] == []


def test_cross_session_ids_are_404_and_untouched(env):
    b1 = _mk('B', task='B 的任务')
    for method, url, body in (('get', f'/api/sessions/A/subagents/{b1.id}', None),
                              ('post', f'/api/sessions/A/subagents/{b1.id}/stop', None),
                              ('post', f'/api/sessions/A/subagents/{b1.id}/send', {'message': '越权'})):
        r = getattr(env.c, method)(url, json=body) if body else getattr(env.c, method)(url)
        assert r.status_code == 404, (url, r.status_code, r.text)
    assert not b1.stop_requested and b1.inbox == [] and b1.status == 'running'


def test_stop(env):
    a = _mk('A')
    r = env.c.post(f'/api/sessions/A/subagents/{a.id}/stop')
    assert r.status_code == 200
    d = r.json()
    assert d['ok'] is True and '已请求停止' in d['message']
    assert a.stop_requested and '界面' in a.stop_reason
    assert d['item']['stop_requested'] is True
    # 已请求停止后不再接收补充指令 (与 agent_control 一致)
    s = env.c.post(f'/api/sessions/A/subagents/{a.id}/send', json={'message': '再做一件事'}).json()
    assert s['ok'] is False and '已被请求停止' in s['error']
    # 已结束的再停: 不报错, 提示无需停止
    reg._finish(a, 'stopped', '[子代理已中断] ...')
    d2 = env.c.post(f'/api/sessions/A/subagents/{a.id}/stop').json()
    assert d2['ok'] is True and '无需停止' in d2['message']


def test_send(env):
    a = _mk('A')
    r = env.c.post(f'/api/sessions/A/subagents/{a.id}/send', json={'message': '  顺便看下 B 模块  '})
    assert r.status_code == 200 and r.json()['ok'] is True
    assert a.inbox == ['顺便看下 B 模块']
    assert r.json()['item']['inbox'] == 1
    # 空 / 超长 (上限与 agent_control 一致) → ok=false, 不入信箱
    e1 = env.c.post(f'/api/sessions/A/subagents/{a.id}/send', json={'message': '   '}).json()
    assert e1['ok'] is False and 'message' in e1['error']
    e2 = env.c.post(f'/api/sessions/A/subagents/{a.id}/send', json={'message': 'x' * (reg.SEND_MAX + 1)}).json()
    assert e2['ok'] is False and str(reg.SEND_MAX) in e2['error']
    ok = env.c.post(f'/api/sessions/A/subagents/{a.id}/send', json={'message': 'y' * reg.SEND_MAX}).json()
    assert ok['ok'] is True and len(a.inbox) == 2
    # 已结束 → 无法送达
    reg._finish(a, 'done', '完成')
    e3 = env.c.post(f'/api/sessions/A/subagents/{a.id}/send', json={'message': '晚了'}).json()
    assert e3['ok'] is False and '已结束' in e3['error']


def test_result_readonly_keeps_unfetched(env):
    a = _mk('A')
    running = env.c.get(f'/api/sessions/A/subagents/{a.id}').json()
    assert running['status'] == 'running' and running['result'] == ''
    reg._finish(a, 'done', '[已验证] 结论: 一切正常')
    d = env.c.get(f'/api/sessions/A/subagents/{a.id}').json()
    assert d['status'] == 'done' and d['result'].startswith('[已验证]') and d['task'] == a.task
    # 用户在界面看结果不算"主代理已取走": 下一轮主代理仍收到 [SYSTEM-AGENTS] 提示
    assert a.fetched is False and a.id in reg.pending_notice('A')
    assert env.c.get('/api/sessions/A/subagents/sa-nope00').status_code == 404


def test_unauthenticated_rejected(env):
    a = _mk('A')
    env.c.cookies.clear()
    n = len(env.calls)
    for method, url in (('get', '/api/sessions/A/subagents'), ('get', f'/api/sessions/A/subagents/{a.id}'),
                        ('post', f'/api/sessions/A/subagents/{a.id}/stop'),
                        ('post', f'/api/sessions/A/subagents/{a.id}/send')):
        assert getattr(env.c, method)(url).status_code == 401, url
    assert len(env.calls) == n and not a.stop_requested    # 未鉴权不转发到 server
    env.c.cookies.set('lc_auth', 'bad-token')
    assert env.c.get('/api/sessions/A/subagents').status_code == 401


def test_other_owner_forbidden(env):
    x = _mk('X')
    assert env.c.get('/api/sessions/X/subagents').status_code == 403
    assert env.c.post(f'/api/sessions/X/subagents/{x.id}/stop').status_code == 403
    assert not x.stop_requested


def test_gateway_requires_bearer(env):
    _mk('A')
    c = TestClient(env.gw)
    assert c.get('/v1/sessions/A/subagents').status_code == 401
    assert c.get('/v1/sessions/A/subagents', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    ok = c.get('/v1/sessions/A/subagents', headers={'Authorization': f'Bearer {TOKEN}'})
    assert ok.status_code == 200 and len(ok.json()['items']) == 1


def test_router_registered_and_gateway_mounted():
    import routers
    from routers import subagents_router as sr, static_router
    found = routers.discover(include_plugins=False)
    assert sr.router in found and found.index(sr.router) < found.index(static_router.router)
    assert sr.SEND_MAX == reg.SEND_MAX
    from gateway import mount
    app = FastAPI()
    mount(app)
    paths = {getattr(r, 'path', '') for r in app.routes}
    assert '/v1/sessions/{sid}/subagents' in paths and '/v1/sessions/{sid}/subagents/{rid}/send' in paths


# ── 前端静态检查 ──
JS = ROOT / 'web_assets' / 'subagent_panel.js'


@pytest.mark.skipif(shutil.which('node') is None, reason='node 不可用')
def test_panel_js_syntax():
    r = subprocess.run(['node', '--check', str(JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_panel_js_rules_and_html_wiring():
    js = JS.read_text(encoding='utf-8')
    code = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    assert not re.search(r'\b(prompt|alert|confirm)\s*\(', code), '禁止弹窗, 用内嵌卡片'
    assert 'document.hidden' in code and 'visibilitychange' in code   # 页面隐藏暂停轮询
    assert '/subagents' in code and 'innerHTML' not in code            # 只用 textContent, 防 XSS
    assert len(js.splitlines()) <= 1000
    html = (ROOT / 'web_ui.html').read_text(encoding='utf-8')
    assert '/assets/subagent_panel.js' in html and '/assets/subagent_panel.css' in html
    assert re.search(r'<script src="/assets/subagent_panel\.js" defer>', html)   # defer: DOM 与 app.js 全局就绪后再跑
    assert 'web_assets2' not in js
