"""GET /api/sessions/{sid} 分页 (?tail=N / ?before=i&limit=N) — 边界、向后兼容、鉴权。

不带参数必须与改动前逐字节一致 (老前端 / CLI / 编辑重试按完整列表定位都依赖它)。
"""
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

N = 25


class FakeWebUI:
    def __init__(self, sessions):
        self.sessions = sessions
        self.loads = 0

    def load_session(self, sid):
        self.loads += 1
        # 真实 load_session 每次从磁盘 json 读出新对象; 这里同样给深拷贝, 防止测试间串改
        return json.loads(json.dumps(self.sessions.get(sid) or {
            "id": sid, "name": "New Chat", "created": 1, "last_used": 1, "messages": []}))


def _msgs(n):
    out = []
    for i in range(n):
        role = 'user' if i % 3 == 0 else 'assistant'
        m = {'role': role, 'content': f'm{i}', 'ts': 1000 + i}
        if role == 'assistant':
            m['tools'] = [f't{i}: {{}}']
            m['_tool_results'] = [{'name': 't', 'result': f'[发送图片: /w/uploads/{i}.png]'}]
        out.append(m)
    return out


@pytest.fixture
def client(monkeypatch):
    from routers import sessions_router as sr
    sess = {'s1': {'id': 's1', 'name': 'n', 'created': 1, 'last_used': 2, 'owner': 'anon', 'messages': _msgs(N)},
            'empty': {'id': 'empty', 'name': 'e', 'created': 1, 'last_used': 2, 'owner': 'anon', 'messages': []},
            'other': {'id': 'other', 'name': 'o', 'created': 1, 'last_used': 2, 'owner': 'bob', 'messages': _msgs(3)}}
    wui = FakeWebUI(sess)
    monkeypatch.setattr(sr, '_wui', lambda: wui)
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)   # 别的用例 import web_ui 可能按真实 config 打开了鉴权
    app = FastAPI()
    app.include_router(sr.router)
    c = TestClient(app)
    c.wui = wui
    c.sess = sess
    return c


def _get(c, q='', sid='s1'):
    r = c.get(f'/api/sessions/{sid}{q}')
    return r.status_code, (r.json() if r.headers.get('content-type', '').startswith('application/json') else r.text)


def test_no_params_fully_backward_compatible(client):
    code, d = _get(client)
    assert code == 200
    assert d == client.sess['s1']                       # 原样: 无 index / total / offset 等新字段
    assert 'total' not in d and all('index' not in m for m in d['messages'])
    # 未知参数 / 只给 limit 也不改变行为
    assert _get(client, '?limit=3')[1] == client.sess['s1']
    assert _get(client, '?foo=1')[1] == client.sess['s1']


def test_tail_basic_and_global_index(client):
    code, d = _get(client, '?tail=5')
    assert code == 200
    assert d['total'] == N and d['offset'] == N - 5 and d['has_more'] is True
    assert [m['index'] for m in d['messages']] == list(range(N - 5, N))
    assert [m['content'] for m in d['messages']] == [f'm{i}' for i in range(N - 5, N)]
    # 其余字段原样保留 (名称 / owner / 工具结果)
    assert d['name'] == 'n' and d['id'] == 's1'
    assert d['messages'][-2]['_tool_results'] == client.sess['s1']['messages'][-2]['_tool_results']


@pytest.mark.parametrize('tail,exp', [(0, []), (1, [N - 1]), (N, list(range(N))), (N + 100, list(range(N)))])
def test_tail_edges(client, tail, exp):
    d = _get(client, f'?tail={tail}')[1]
    assert [m['index'] for m in d['messages']] == exp
    assert d['total'] == N
    assert d['offset'] == max(0, N - tail) and d['has_more'] is (max(0, N - tail) > 0)


@pytest.mark.parametrize('before,limit,exp', [
    (10, 3, [7, 8, 9]),
    (3, 10, [0, 1, 2]),          # 不足 limit: 从 0 开始
    (0, 5, []),                  # 已到开头
    (N, 4, list(range(N - 4, N))),
    (N + 50, 4, list(range(N - 4, N))),   # 越界 before 钳到 total
    (10, 0, []),
])
def test_before_limit_edges(client, before, limit, exp):
    d = _get(client, f'?before={before}&limit={limit}')[1]
    assert [m['index'] for m in d['messages']] == exp
    offset = max(0, min(before, N) - limit)
    assert d['offset'] == offset and d['has_more'] is (offset > 0) and d['total'] == N


def test_before_default_limit_is_60(client):
    client.sess['s1']['messages'] = _msgs(150)
    d = _get(client, '?before=100')[1]
    assert [m['index'] for m in d['messages']] == list(range(40, 100))


def test_pages_tile_the_full_list_without_gap_or_overlap(client):
    client.sess['s1']['messages'] = _msgs(137)
    d = _get(client, '?tail=60')[1]
    got = d['messages']
    while d['has_more']:
        d = _get(client, f"?before={d['offset']}&limit=60")[1]
        got = d['messages'] + got
    assert [m['index'] for m in got] == list(range(137))
    full = _get(client)[1]['messages']
    assert [{k: v for k, v in m.items() if k != 'index'} for m in got] == full


def test_empty_session(client):
    d = _get(client, '?tail=60', sid='empty')[1]
    assert d['messages'] == [] and d['total'] == 0 and d['offset'] == 0 and d['has_more'] is False
    d = _get(client, '?before=0&limit=5', sid='empty')[1]
    assert d['messages'] == [] and d['total'] == 0


@pytest.mark.parametrize('q', ['?tail=-1', '?tail=abc', '?before=-3', '?before=x&limit=2', '?before=5&limit=-1',
                               '?tail=2&before=5'])
def test_invalid_params_400(client, q):
    assert _get(client, q)[0] == 400


def test_paging_does_not_mutate_cached_session(client):
    _get(client, '?tail=3')
    assert all('index' not in m for m in client.sess['s1']['messages'])


def test_owner_still_enforced_with_paging(client, monkeypatch):
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'tok': time.time() + 100})
    client.cookies.set('lc_auth', 'tok')
    assert _get(client, '?tail=2', sid='other')[0] == 403
    assert _get(client, '?before=2&limit=1', sid='other')[0] == 403


def test_auth_required_same_as_unpaged(client, monkeypatch):
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'tok': time.time() + 100})
    for q in ('', '?tail=5', '?before=10&limit=2'):
        assert _get(client, q)[0] == 401
    client.cookies.set('lc_auth', 'tok')
    for q in ('', '?tail=5', '?before=10&limit=2'):
        assert _get(client, q)[0] == 200


def test_paging_keeps_server_session_reimport(tmp_path, monkeypatch):
    """真实 web_ui.load_session: server 会话比 web 缓存新 → 照常整份重导并写回缓存, 分页只切片结果。"""
    import os
    import web_ui
    from routers import sessions_router as sr
    web_dir, srv_dir = tmp_path / 'web', tmp_path / 'srv'
    web_dir.mkdir()
    srv_dir.mkdir()
    monkeypatch.setattr(web_ui, 'SESSIONS_DIR', web_dir)
    monkeypatch.setattr(web_ui, 'CFG', {**web_ui.CFG, 'paths': {'workspace_base': str(tmp_path),
                                                                'sessions_dir': str(srv_dir)}})
    monkeypatch.setattr(web_ui, '_TOMBSTONE_FILE', tmp_path / 'tomb.json')
    monkeypatch.setattr(sr, '_wui', lambda: web_ui)
    sid = 'cli-x'
    (web_dir / f'{sid}.json').write_text(json.dumps({'id': sid, 'name': 'old', 'messages': [
        {'role': 'user', 'content': '旧缓存'}, {'role': 'assistant', 'content': '旧答', 'tools': ['a: 1']}]}))
    old = time.time() - 100
    os.utime(web_dir / f'{sid}.json', (old, old))
    srv = [{'role': 'system', 'content': 's'}]
    for i in range(5):
        srv += [{'role': 'user', 'content': f'问{i}'}, {'role': 'assistant', 'content': f'答{i}'}]
    (srv_dir / f'{sid}.json').write_text(json.dumps({'msgs': srv, 'ts': time.time()}, ensure_ascii=False))
    app = FastAPI()
    app.include_router(sr.router)
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)   # import web_ui 会按真实 config 打开鉴权
    d = TestClient(app).get(f'/api/sessions/{sid}?tail=3').json()
    assert d['total'] == 10 and [m['content'] for m in d['messages']] == ['答3', '问4', '答4']
    assert [m['index'] for m in d['messages']] == [7, 8, 9]
    cached = json.loads((web_dir / f'{sid}.json').read_text())
    assert len(cached['messages']) == 10 and all('index' not in m for m in cached['messages'])
