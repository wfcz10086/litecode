"""[ui-audit 2026-10-21] Web 接口异常输入 / 不存在资源 应回清楚的 4xx 而不是 500 或假 200。

覆盖实测发现的问题:
- 非法 JSON / 非对象 JSON 请求体 → 原来 500 (JSONDecodeError / AttributeError)
- 不存在的 sid: GET/PATCH/导出/分享 原来 200 (PATCH 还会凭空建出会话)
- 超长消息原来不限, 整段塞进模型上下文
- /api/bgprocs/{pid}/tail 前端在调, 后端没有 (404)
- 删除不存在的模型回 ok 并重写 config.json; 切换不存在的模型回 200 + error
- 轮次结束落库时 usage 丢失 (刷新后 P/C/T · 耗时 统计没了)
- drawflow 走 CDN (外网慢时首屏卡住) + CSP 放行 jsdelivr
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)


# ── json_object helper ───────────────────────────────────────────

def _body_app():
    from fastapi import Request
    from routers._body import json_object
    app = FastAPI()

    @app.post('/echo')
    async def echo(request: Request):
        return await json_object(request)
    return TestClient(app)


@pytest.mark.parametrize('raw,code', [
    (b'{not json', 400), (b'[1,2]', 400), (b'"x"', 400), (b'null', 400), (b'', 400), (b'   ', 400),
    (b'\xff\xfe', 400), (b'{"a":1}', 200),
])
def test_json_object(raw, code):
    r = _body_app().post('/echo', content=raw, headers={'Content-Type': 'application/json'})
    assert r.status_code == code
    if code == 400:
        assert r.json()['detail']   # 有可读说明


# ── sessions_router: 不存在的会话 → 404 ──────────────────────────

class FakeWebUI:
    def __init__(self, root: Path, sessions: dict):
        self.root = root
        self.sessions = sessions
        root.mkdir(parents=True, exist_ok=True)
        for sid, d in sessions.items():
            (root / f'{sid}.json').write_text(json.dumps(d))

    def _sf(self, sid):
        return self.root / f'{sid}.json'

    def load_session(self, sid):
        d = self.sessions.get(sid)
        if d:
            return json.loads(json.dumps(d))
        return {'id': sid, 'name': 'New Chat', 'created': 1, 'last_used': 1, 'messages': []}

    def save_session(self, d):
        self.sessions[d['id']] = d
        self._sf(d['id']).write_text(json.dumps(d))


@pytest.fixture
def sess_client(tmp_path, monkeypatch):
    from routers import sessions_router as sr
    wui = FakeWebUI(tmp_path / 'web', {'s1': {'id': 's1', 'name': '周报/草稿: v2', 'created': 1, 'last_used': 2,
                                              'owner': 'anon', 'messages': [{'role': 'user', 'content': 'hi'}]}})
    monkeypatch.setattr(sr, '_wui', lambda: wui)
    monkeypatch.setattr(sr.S, 'cfg', {'paths': {'sessions_dir': str(tmp_path / 'srv')}})
    app = FastAPI()
    app.include_router(sr.router)
    c = TestClient(app)
    c.wui = wui
    return c


def test_missing_session_404(sess_client):
    c = sess_client
    assert c.get('/api/sessions/s1').status_code == 200
    assert c.get('/api/sessions/nope').status_code == 404
    assert c.get('/api/sessions/nope/export').status_code == 404
    r = c.patch('/api/sessions/nope', json={'name': 'x'})
    assert r.status_code == 404
    assert not c.wui._sf('nope').exists()          # 原来 PATCH 会凭空建出会话文件
    assert c.post('/api/sessions/nope/share', json={}).status_code == 404
    assert c.post('/api/sessions/nope/truncate', json={'keep': 0}).status_code == 404


def test_server_only_session_counts_as_existing(sess_client, tmp_path):
    srv = tmp_path / 'srv'
    srv.mkdir()
    (srv / 'cli-1.json').write_text('{}')
    from routers.sessions_router import session_exists
    assert session_exists('cli-1', sess_client.wui)
    assert not session_exists('../etc', sess_client.wui)


def test_bad_bodies_are_400(sess_client):
    c = sess_client
    bad = {'content': b'{oops', 'headers': {'Content-Type': 'application/json'}}
    assert c.post('/api/sessions', **bad).status_code == 400
    assert c.patch('/api/sessions/s1', **bad).status_code == 400
    assert c.post('/api/sessions/s1/truncate', **bad).status_code == 400
    assert c.post('/api/sessions/s1/truncate', json={'keep': 'abc'}).status_code == 400
    assert c.post('/api/sessions/s1/share', json={'ttl_sec': 'abc'}).status_code == 400
    assert c.post('/api/sessions/s1/share', json={'ttl_sec': -5}).status_code == 400


def test_export_filename_uses_title(sess_client):
    r = sess_client.get('/api/sessions/s1/export')
    assert r.status_code == 200
    cd = r.headers['content-disposition']
    assert 'filename="s1.md"' in cd                      # ASCII 兜底
    assert "filename*=UTF-8''" in cd and '/' not in cd.split("''", 1)[1]
    assert '%E5%91%A8%E6%8A%A5' in cd                    # "周报"


# ── chat_router: 消息校验 ───────────────────────────────────────

@pytest.fixture
def chat_client(monkeypatch):
    from routers import chat_router as cr
    import lib.rate_limit as rl
    monkeypatch.setattr(rl, 'rate_check', lambda *a, **k: None)
    app = FastAPI()
    app.include_router(cr.router)
    return TestClient(app), cr


def test_chat_rejects_bad_and_huge_messages(chat_client):
    c, cr = chat_client
    assert c.post('/api/chat/s1', content=b'{x', headers={'Content-Type': 'application/json'}).status_code == 400
    assert c.post('/api/chat/s1', json=[]).status_code == 400
    assert c.post('/api/chat/s1', json={'message': ''}).status_code == 400
    assert c.post('/api/chat/s1', json={'message': None}).status_code == 400
    r = c.post('/api/chat/s1', json={'message': 'x' * (cr.MAX_MESSAGE_CHARS + 1)})
    assert r.status_code == 413 and '上限' in r.json()['detail']


# ── health_router: /api/bgprocs/{pid}/tail ─────────────────────

@pytest.fixture
def bg_client(tmp_path, monkeypatch):
    from routers import health_router as hr
    log = tmp_path / 'bg.log'
    log.write_text('\n'.join(f'line{i}' for i in range(500)))

    async def aproxy(path, method='GET', **kw):
        assert path == '/v1/bgprocs'
        return {'procs': [{'pid': '4242', 'log': str(log)}, {'pid': '7', 'log': ''}]}
    monkeypatch.setattr(hr.S, 'aproxy', aproxy)
    app = FastAPI()
    app.include_router(hr.router)
    return TestClient(app)


def test_bgproc_tail(bg_client):
    d = bg_client.get('/api/bgprocs/4242/tail?lines=3').json()
    assert d['log'].splitlines() == ['line497', 'line498', 'line499']
    assert bg_client.get('/api/bgprocs/7/tail').json()['log'] == ''
    assert bg_client.get('/api/bgprocs/999/tail').status_code == 404
    assert bg_client.get('/api/bgprocs/abc/tail').status_code == 400


# ── model_router: 上游 4xx 透传 / 删除不存在的模型 ─────────────────

@pytest.fixture
def model_client(monkeypatch):
    from routers import model_router as mr
    calls = []

    async def aproxy(path, method='GET', **kw):
        calls.append((method, path))
        if path == '/v1/model/list':
            return {'active': 'a', 'models': [{'id': 'a'}, {'id': 'b'}]}
        if path == '/v1/model/switch':
            return {'error': 'HTTP 404', 'status': 404}
        return {'ok': True}
    monkeypatch.setattr(mr.S, 'aproxy', aproxy)
    app = FastAPI()
    app.include_router(mr.router)
    c = TestClient(app)
    c.calls = calls
    return c


def test_model_delete_and_switch_status(model_client):
    c = model_client
    assert c.delete('/api/models/zzz').status_code == 404
    assert ('DELETE', '/v1/model/zzz') not in c.calls     # 不存在就不去碰上游 (上游会整份重写 config.json)
    assert c.delete('/api/models/b').json()['ok']
    assert c.post('/api/models/switch', json={'model_id': 'zzz'}).status_code == 404
    assert c.post('/api/models/switch', content=b'{', headers={'Content-Type': 'application/json'}).status_code == 400


# ── auth: 登录请求体非法 JSON ───────────────────────────────────

def test_login_bad_json_400(monkeypatch):
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_login_fails', {})
    monkeypatch.setattr(auth, '_login_lock_until', {})
    app = FastAPI()
    app.include_router(auth.router)
    r = TestClient(app).post('/api/login', content=b'{x', headers={'Content-Type': 'application/json'})
    assert r.status_code == 400


# ── web_ui._save_assistant_msg: 已重导的轮次也要挂上 usage ────────────

def test_usage_attached_when_turn_already_imported(monkeypatch):
    import web_ui
    store = {'id': 's', 'messages': [{'role': 'user', 'content': 'q'},
                                     {'role': 'assistant', 'content': '收到', 'tools': [], '_tool_results': []}]}
    saved = []
    monkeypatch.setattr(web_ui, 'load_session', lambda sid: json.loads(json.dumps(store)))
    monkeypatch.setattr(web_ui, 'save_session', lambda d: saved.append(d))
    usage = {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12, 'elapsed_seconds': 0.6}
    web_ui._save_assistant_msg('s', {'text': '收到', 'reasoning': '', 'tools': [], 'diffs': [],
                                     'usage': usage, 'desktop_steps': []})
    assert saved and saved[-1]['messages'][-1]['usage'] == usage
    assert len(saved[-1]['messages']) == 2               # 不重复追加


# ── 静态: 不再依赖 CDN ─────────────────────────────────────────

def test_no_cdn_scripts():
    html = (ROOT / 'web_ui.html').read_text(encoding='utf-8')
    assert 'https://cdn.' not in html and 'src="http' not in html
    assert (ROOT / 'web_assets' / 'vendor' / 'drawflow.min.js').stat().st_size > 10000
    from routers.static_router import PAGE_CSP
    assert 'jsdelivr' not in PAGE_CSP


# ── web_ui: 重导 server 会话时保留用户起的名字 / owner, web 会话不加 🔌 ─────

def test_reimport_keeps_cached_name_and_owner(tmp_path):
    import web_ui
    cache = tmp_path / 'web-abc.json'
    cache.write_text(json.dumps({'id': 'web-abc', 'name': '我的周报', 'owner': 'admin', 'created': 5, 'messages': []}))
    d = web_ui._convert_server_session('web-abc', {'msgs': [{'role': 'user', 'content': '帮我写周报'},
                                                            {'role': 'assistant', 'content': '好'}]})
    assert not d['name'].startswith('🔌')
    web_ui._merge_cached_reasoning(cache, d)
    assert d['name'] == '我的周报' and d['owner'] == 'admin' and d['created'] == 5
    cache.write_text(json.dumps({'id': 'web-abc', 'name': 'New Chat', 'messages': []}))
    d2 = web_ui._convert_server_session('web-abc', {'msgs': [{'role': 'user', 'content': '帮我写周报'}]})
    web_ui._merge_cached_reasoning(cache, d2)
    assert d2['name'] == '帮我写周报'                      # 默认名才用首条提问
    assert web_ui._convert_server_session('cli-1', {'msgs': [{'role': 'user', 'content': 'x'}]})['name'].startswith('🖥️')
