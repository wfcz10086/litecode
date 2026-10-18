"""子代理面板轮询的会话归属校验缓存 (lib/owner.require_owner_cached + routers/subagents_router._guard)。

会话存在 tmp 盘上 (仿 web_ui 的 web_sessions / server sessions / 墓碑文件), load_session 计数:
命中缓存不再读盘; 会话被改写 (转移 owner) / 删除 / 进墓碑 → 指纹变化立即失效; TTL 到期重查;
别人的会话每次都拒绝且从不进缓存; 不同用户不共享缓存项。
"""
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib import owner as own  # noqa: E402


class DiskWebUI:
    """与 web_ui 同形: _sf / CFG.paths / _TOMBSTONE_FILE / load_session (真读盘, 计数)。"""

    def __init__(self, base: Path):
        self.web = base / 'web_sessions'
        self.web.mkdir()
        ws = base / 'ws'
        (ws / 'sessions').mkdir(parents=True)
        self.srv = ws / 'sessions'
        self.CFG = {'paths': {'workspace_base': str(ws)}}
        self._TOMBSTONE_FILE = base / 'deleted_sids.json'
        self.loads = 0

    def _sf(self, sid):
        return self.web / f'{sid}.json'

    def write(self, sid, owner):
        p = self._sf(sid)
        p.write_text(json.dumps({'id': sid, 'messages': [], 'owner': owner}))
        st = p.stat()   # 保证 mtime 前进 (粗粒度文件系统上同一秒内两次写)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def load_session(self, sid):
        self.loads += 1
        p = self._sf(sid)
        if p.exists():
            return json.loads(p.read_text())
        return {'id': sid, 'messages': []}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from routers import subagents_router as sr
    from routers.state import S
    import plugins.auth.core as auth
    wui = DiskWebUI(tmp_path)
    monkeypatch.setattr(sr, '_wui', lambda: wui)
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'good': time.time() + 3600})

    async def aproxy(path, method='GET', **kw):
        return {'ok': True, 'items': [], 'running': 0}
    monkeypatch.setattr(S, 'aproxy', aproxy, raising=False)
    own.clear_owner_cache()
    app = FastAPI()
    app.include_router(sr.router)
    c = TestClient(app)
    c.cookies.set('lc_auth', 'good')       # 当前用户 = admin (与 anon 互通)
    yield SimpleNamespace(c=c, wui=wui, app=app)
    own.clear_owner_cache()


def test_hit_skips_disk_read(env):
    env.wui.write('A', 'anon')
    for _ in range(5):
        assert env.c.get('/api/sessions/A/subagents').status_code == 200
    assert env.wui.loads == 1                 # 只有第一次读盘
    assert env.c.post('/api/sessions/A/subagents/x/stop').status_code == 200   # 同 (用户, sid) 其它接口也命中
    assert env.wui.loads == 1


def test_transfer_invalidates_and_denies(env):
    env.wui.write('A', 'anon')
    assert env.c.get('/api/sessions/A/subagents').status_code == 200
    env.wui.write('A', 'bob')                 # 转移给别人 → 文件 mtime 变 → 失效
    assert env.c.get('/api/sessions/A/subagents').status_code == 403
    assert env.wui.loads == 2


def test_delete_invalidates(env):
    env.wui.write('A', 'anon')
    assert env.c.get('/api/sessions/A/subagents').status_code == 200
    env.wui._sf('A').unlink()                 # 删除 → 指纹变 → 重查 (load_session 返回空会话)
    env.c.get('/api/sessions/A/subagents')
    assert env.wui.loads == 2
    env.wui._TOMBSTONE_FILE.write_text('["A"]')   # 进墓碑也失效
    env.c.get('/api/sessions/A/subagents')
    assert env.wui.loads == 3


def test_other_owner_never_cached(env):
    env.wui.write('X', 'bob')
    for _ in range(3):
        assert env.c.get('/api/sessions/X/subagents').status_code == 403
    assert env.wui.loads == 3                 # 拒绝每次都真查
    assert not [k for k in own._owner_cache if k[1] == 'X']


def test_ttl_expiry(env, monkeypatch):
    env.wui.write('A', 'anon')
    t = [1000.0]
    monkeypatch.setattr(own._time, 'monotonic', lambda: t[0])
    env.c.get('/api/sessions/A/subagents')
    t[0] += own.OWNER_CACHE_TTL - 1
    env.c.get('/api/sessions/A/subagents')
    assert env.wui.loads == 1
    t[0] += 2
    env.c.get('/api/sessions/A/subagents')
    assert env.wui.loads == 2


def test_unauthenticated_still_rejected_and_keyed_by_user(env, monkeypatch):
    env.wui.write('A', 'admin')
    assert env.c.get('/api/sessions/A/subagents').status_code == 200
    assert TestClient(env.app).get('/api/sessions/A/subagents').status_code == 401   # 未登录先被 require_auth 拦
    # 缓存按用户分: 同 sid 换个用户身份不会命中 admin 的缓存项
    monkeypatch.setattr(own, 'current_user', lambda req: 'bob')
    assert env.c.get('/api/sessions/A/subagents').status_code == 403


def test_helper_unit_stamp_error_not_cached():
    own.clear_owner_cache()
    n = [0]

    def load(sid):
        n[0] += 1
        return {'owner': 'anon'}

    def bad_stamp(sid):
        raise OSError('boom')
    for _ in range(3):
        own.require_owner_cached('s', None, load, bad_stamp)
    assert n[0] == 3 and not own._owner_cache
    with pytest.raises(HTTPException):
        own.require_owner_cached('s', None, lambda sid: {'owner': 'bob'}, lambda sid: 1)
