"""仓外插件挂 Web 路由: plugin.py 可选导出 ROUTER / ROUTER_ORDER / ROUTER_PUBLIC (ABI v1 向后兼容扩展)。"""
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

import routers                                   # noqa: E402
from routers import static_router                # noqa: E402
from plugins.registry import registry            # noqa: E402
from plugins.auth import core as auth            # noqa: E402

PING = textwrap.dedent('''
    from fastapi import APIRouter
    ROUTER = APIRouter(prefix="/api/plugin-demo")
    STATE = {"hits": 0}

    @ROUTER.get("/ping")
    def ping():
        STATE["hits"] += 1
        return {"pong": True, "hits": STATE["hits"]}
''')


def _plugin(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir()
    (d / 'plugin.py').write_text(body)
    return d


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    """造插件目录 → 设 LITECODE_PLUGIN_PATH → 重扫; 结束后把全局 registry 复位为未扫描。"""
    def setup(*dirs):
        monkeypatch.setenv('LITECODE_PLUGIN_PATH', ':'.join(str(d) for d in dirs))
        registry.scan()
    yield setup
    monkeypatch.delenv('LITECODE_PLUGIN_PATH', raising=False)
    registry._plugins.clear()
    registry._ext_modules.clear()
    registry._load_errors.clear()
    registry._bridge = None
    registry._scanned = False


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'good-token': 9e18})


def _app(routers_list):
    app = FastAPI()
    for r in routers_list:
        app.include_router(r)
    return app


def test_no_env_no_plugin_routers(monkeypatch):
    monkeypatch.delenv('LITECODE_PLUGIN_PATH', raising=False)
    assert routers.discover_plugin_routers() == []


def test_plugin_router_collected_before_static(tmp_path, plugin_env):
    d = _plugin(tmp_path, 'demo', PING + 'ROUTER_ORDER = 5000\n')   # 超大 ORDER 也要钳到 static 之前
    plugin_env(d)
    extra = routers.discover_plugin_routers()
    assert [(o, n) for o, n, _ in extra] == [(routers.PLUGIN_MAX_ORDER, 'plugin:demo')]
    found = routers.discover()
    assert found[-1] is static_router.router
    assert extra[0][2] in found
    paths = [getattr(rt, 'path', '') for r in found for rt in r.routes]
    assert paths.index('/api/plugin-demo/ping') < paths.index('/assets/{name:path}')


def test_plugin_module_reused_not_reimported(tmp_path, plugin_env, monkeypatch):
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)
    d = _plugin(tmp_path, 'demo', PING)
    plugin_env(d)
    (_, mod), = registry.external_modules()
    client = TestClient(_app(routers.discover(include_plugins=True)))
    assert client.get('/api/plugin-demo/ping').json() == {'pong': True, 'hits': 1}
    assert mod.STATE['hits'] == 1                    # 路由跑的正是 registry 导入的那份模块


def test_default_requires_login(tmp_path, plugin_env, auth_on):
    d = _plugin(tmp_path, 'demo', PING)
    plugin_env(d)
    client = TestClient(_app(r for _, _, r in routers.discover_plugin_routers()))
    assert client.get('/api/plugin-demo/ping').status_code == 401
    client.cookies.set(auth._COOKIE_NAME, 'good-token')
    r = client.get('/api/plugin-demo/ping')
    assert r.status_code == 200 and r.json()['pong'] is True


def test_public_router_open(tmp_path, plugin_env, auth_on):
    d = _plugin(tmp_path, 'demo', PING + 'ROUTER_PUBLIC = True\n')
    plugin_env(d)
    client = TestClient(_app(r for _, _, r in routers.discover_plugin_routers()))
    assert client.get('/api/plugin-demo/ping').status_code == 200


def test_bad_plugins_skipped(tmp_path, plugin_env, monkeypatch):
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)
    bad_import = _plugin(tmp_path, 'bad_import', 'raise RuntimeError("boom")\n')
    bad_type = _plugin(tmp_path, 'bad_type', 'ROUTER = "not a router"\n')
    bad_order = _plugin(tmp_path, 'bad_order', PING.replace('plugin-demo', 'plugin-b') + 'ROUTER_ORDER = "x"\n')
    good = _plugin(tmp_path, 'good', PING)
    tool_only = _plugin(tmp_path, 'tool_only', textwrap.dedent('''
        NAME = "demo_tool_only"
        DESCRIPTION = "d"
        SCHEMA = {"type": "object", "properties": {}}
        async def run(args, ctx):
            return {"ok": True}
    '''))
    plugin_env(bad_import, bad_type, bad_order, good, tool_only)
    extra = routers.discover_plugin_routers()             # 不抛
    assert sorted((o, n) for o, n, _ in extra) == [(500, 'plugin:bad_order'), (500, 'plugin:good')]
    assert registry.get('demo_tool_only') is not None     # 老式纯工具插件不受影响
    client = TestClient(_app(routers.discover()))
    assert client.get('/api/plugin-demo/ping').status_code == 200
    assert client.get('/api/plugin-b/ping').status_code == 200
