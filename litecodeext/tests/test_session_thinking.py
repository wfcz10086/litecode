"""[session-thinking 2026-10-21] 输入框 🧠 只作用于当前会话, 不再改全局 config。

- lib.session_model: 存取 / 非法档位 / 跟随默认
- lib.config.set_turn_model(extra=...): 只叠思考键时其它模型参数仍是全局值; 复位后回到全局
- thinking_adapter 在覆盖层生效期间读到的是会话档位 (端到端: 注入请求体的参数)
- gateway.with_turn_model: 只有思考覆盖时不多发 session_model 状态帧
- API: GET/PUT /api/sessions/{sid}/thinking (校验 / 404 / 不写 config)
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

from lib import config, session_model as sm  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, 'THINK_STORE', tmp_path / 'session_thinking.json')
    config._TURN_MODEL.set(None)
    yield tmp_path
    config._TURN_MODEL.set(None)


def test_store_roundtrip(store):
    assert sm.get_thinking('s1') == '' and sm.thinking_overlay('s1') is None
    sm.set_thinking('s1', 'high')
    assert sm.get_thinking('s1') == 'high'
    assert sm.thinking_overlay('s1') == {'enable_thinking': True, 'thinking_budget': 16384}
    assert sm.get_thinking('s2') == ''                       # 别的会话不受影响
    sm.set_thinking('s1', None)
    assert sm.get_thinking('s1') == ''
    with pytest.raises(ValueError):
        sm.set_thinking('s1', 'ultra')
    (store / 'session_thinking.json').write_text(json.dumps({'s3': 'bogus'}))
    assert sm.get_thinking('s3') == ''                       # 脏数据当没指定


def test_level_of():
    assert sm.level_of(False, 4096) == 'off'
    assert sm.level_of(True, 2048) == 'low' and sm.level_of(True, 4096) == 'mid' and sm.level_of(True, 16384) == 'high'


def test_turn_overlay_only_thinking(store, monkeypatch):
    monkeypatch.setitem(config._LIVE, 'enable_thinking', True)
    monkeypatch.setitem(config._LIVE, 'thinking_budget', 4096)
    monkeypatch.setitem(config._LIVE, 'model_id', 'glob')
    tok = config.set_turn_model(None, {'enable_thinking': False, 'thinking_budget': 0})
    try:
        assert config._LIVE.get('enable_thinking') is False and config._LIVE['thinking_budget'] == 0
        assert config._LIVE.get('model_id') == 'glob'          # 只覆盖思考键
        from lib import thinking_adapter as ta
        assert ta._live_cfg().enabled is False
    finally:
        config.reset_turn_model(tok)
    assert config._LIVE.get('enable_thinking') is True
    assert config.set_turn_model(None, None) is not None       # 无覆盖 → 置空
    assert config._TURN_MODEL.get() is None


def test_with_turn_model_thinking_only_no_status_frame(store, monkeypatch):
    from gateway import openai_compat as oc
    monkeypatch.setitem(config._LIVE, 'enable_thinking', True)
    seen = []

    async def gen():
        seen.append(config._LIVE.get('enable_thinking'))
        yield 'data: a\n\n'
        seen.append(config._LIVE.get('enable_thinking'))
        yield 'data: b\n\n'

    async def run():
        return [c async for c in oc.with_turn_model(gen(), {}, '', 'glob', thinking={'enable_thinking': False})]
    out = asyncio.run(run())
    assert out == ['data: a\n\n', 'data: b\n\n']                # 没有多余的 session_model 帧
    assert seen == [False, False]
    assert config._LIVE.get('enable_thinking') is True          # 结束复位


@pytest.fixture
def api(store, monkeypatch, tmp_path):
    from routers import model_router as mr

    class W:
        def _sf(self, sid):
            p = tmp_path / f'{sid}.json'
            return p
        def load_session(self, sid):
            return {'id': sid, 'messages': [], 'owner': 'anon'}
    (tmp_path / 'web-x.json').write_text('{}')
    monkeypatch.setattr(mr, '_wui', lambda: W())
    monkeypatch.setattr(mr.S, 'cfg', {'paths': {'sessions_dir': str(tmp_path / 'srv')}})
    monkeypatch.setattr(mr, '_global_thinking_level', lambda: 'mid')
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)
    app = FastAPI()
    app.include_router(mr.router)
    return TestClient(app)


def test_api(api):
    d = api.get('/api/sessions/web-x/thinking').json()
    assert d['follows_default'] and d['effective'] == 'mid' and d['level'] is None
    d = api.put('/api/sessions/web-x/thinking', json={'level': 'off'}).json()
    assert d['ok'] and d['effective'] == 'off' and not d['follows_default']
    assert sm.get_thinking('web-x') == 'off'
    assert api.put('/api/sessions/web-x/thinking', json={'level': 'ultra'}).status_code == 400
    assert api.put('/api/sessions/web-x/thinking', json={}).status_code == 400
    assert api.put('/api/sessions/web-x/thinking', content=b'{', headers={'Content-Type': 'application/json'}).status_code == 400
    assert api.get('/api/sessions/web-nope/thinking').status_code == 404
    d = api.put('/api/sessions/web-x/thinking', json={'level': None}).json()
    assert d['follows_default'] and sm.get_thinking('web-x') == ''
