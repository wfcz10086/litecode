"""仓外插件自带前端资源: plugin.py 可选导出 WEB_ASSETS / WEB_ENTRY (ABI v1 向后兼容扩展)。"""
import logging
import os
import re
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

import routers                                       # noqa: E402
from routers import plugin_assets_router as pa       # noqa: E402
from routers import static_router                    # noqa: E402
from routers.state import S                          # noqa: E402
from plugins.registry import registry                # noqa: E402

PAGE = '<html><head></head><body><div id="sbf"></div>\n</body></html>'


def _plugin(root: Path, name: str, body: str, files: dict | None = None) -> Path:
    d = root / name
    d.mkdir()
    (d / 'plugin.py').write_text(textwrap.dedent(body))
    for rel, content in (files or {}).items():
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content if isinstance(content, bytes) else content.encode())
    return d


@pytest.fixture
def plugin_env(monkeypatch):
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
def client(monkeypatch):
    monkeypatch.setattr(S, 'get_html', lambda: PAGE)
    app = FastAPI()
    app.include_router(pa.router)
    app.include_router(static_router.router)
    return TestClient(app)


GOOD = '''
    WEB_ASSETS = "web"
    WEB_ENTRY = ["panel.css", "panel.js"]
'''
GOOD_FILES = {'web/panel.js': 'console.log(1)', 'web/panel.css': 'body{}',
              'web/img/logo.png': b'\x89PNG', 'web/secret.py': 'x=1', 'web/.env': 'K=V'}


# ── 无插件: 主页字节不变 ─────────────────────────────────────

def test_no_env_page_unchanged(client, monkeypatch):
    monkeypatch.delenv('LITECODE_PLUGIN_PATH', raising=False)
    assert pa.collect_web_plugins() == {}
    assert pa.inject_html(PAGE) is PAGE
    r = client.get('/')
    assert r.status_code == 200 and r.content == PAGE.encode()


def test_plugins_without_web_fields_page_unchanged(tmp_path, plugin_env, client):
    """老插件 (只有 NAME/run 或只有 ROUTER) 不导出 WEB_*: 主页不变, 也不能被托管。"""
    d = _plugin(tmp_path, 'old', '''
        NAME = "old_tool"
        DESCRIPTION = "x"
        SCHEMA = {"type": "object", "properties": {}}
        async def run(args, ctx):
            return {"ok": True, "result": ""}
    ''', {'web/a.js': 'x'})
    plugin_env(d)
    assert pa.collect_web_plugins() == {}
    assert client.get('/').content == PAGE.encode()
    assert client.get('/plugin-assets/old/web/a.js').status_code == 404


def test_assets_only_without_entry_page_unchanged(tmp_path, plugin_env, client):
    d = _plugin(tmp_path, 'quiet', 'WEB_ASSETS = "web"\n', {'web/a.js': 'x'})
    plugin_env(d)
    assert client.get('/').content == PAGE.encode()
    assert client.get('/plugin-assets/quiet/a.js').status_code == 200


# ── 托管 ────────────────────────────────────────────────────

def test_serve_assets(tmp_path, plugin_env, client):
    d = _plugin(tmp_path, 'hello', GOOD, GOOD_FILES)
    plugin_env(d)
    r = client.get('/plugin-assets/hello/panel.js')
    assert r.status_code == 200 and r.text == 'console.log(1)'
    assert r.headers['content-type'].startswith('application/javascript')
    assert r.headers['x-content-type-options'] == 'nosniff'
    assert client.get('/plugin-assets/hello/panel.css').headers['content-type'].startswith('text/css')
    assert client.get('/plugin-assets/hello/img/logo.png').content == b'\x89PNG'
    assert client.get('/plugin-assets/hello/missing.js').status_code == 404
    assert client.get('/plugin-assets/nope/panel.js').status_code == 404
    assert client.get('/plugin-assets/he%3Cllo/panel.js').status_code == 404


def test_absolute_web_assets_dir(tmp_path, plugin_env, client):
    web = tmp_path / 'elsewhere'
    web.mkdir()
    (web / 'abs.js').write_text('ok')
    d = _plugin(tmp_path, 'absp', f'WEB_ASSETS = {str(web)!r}\nWEB_ENTRY = ["abs.js"]\n')
    plugin_env(d)
    assert client.get('/plugin-assets/absp/abs.js').text == 'ok'


def test_illegal_suffix_and_hidden_rejected(tmp_path, plugin_env, client):
    d = _plugin(tmp_path, 'hello', GOOD, GOOD_FILES)
    plugin_env(d)
    assert client.get('/plugin-assets/hello/secret.py').status_code == 400
    assert client.get('/plugin-assets/hello/.env').status_code == 400
    assert client.get('/plugin-assets/hello/img/.hidden.png').status_code == 400


@pytest.mark.parametrize('path', [
    '%2e%2e/plugin.py', '..%2fplugin.js', 'web/%2e%2e/%2e%2e/x.js', '%2e%2e%2f%2e%2e%2fetc%2fpasswd.txt',
    '%5c..%5cplugin.js', 'a//b.js', '%2fetc%2fhosts.txt',
])
def test_path_traversal_rejected(tmp_path, plugin_env, client, path):
    d = _plugin(tmp_path, 'hello', GOOD, GOOD_FILES)
    (tmp_path / 'plugin.js').write_text('outside')
    plugin_env(d)
    r = client.get(f'/plugin-assets/hello/{path}')
    assert r.status_code == 400
    assert 'outside' not in r.text


def test_symlink_escape_404(tmp_path, plugin_env, client):
    d = _plugin(tmp_path, 'hello', GOOD, GOOD_FILES)
    (tmp_path / 'outside.js').write_text('outside')
    os.symlink(tmp_path / 'outside.js', d / 'web' / 'link.js')
    plugin_env(d)
    r = client.get('/plugin-assets/hello/link.js')
    assert r.status_code == 404 and 'outside' not in r.text


# ── 注入 ────────────────────────────────────────────────────

def test_inject_order_and_tags(tmp_path, plugin_env, client):
    b = _plugin(tmp_path, 'beta', 'WEB_ASSETS = "w"\nWEB_ENTRY = ["b.js"]\n', {'w/b.js': 'b'})
    a = _plugin(tmp_path, 'alpha', 'WEB_ASSETS = "w"\nWEB_ENTRY = ("a.css", "a.js", "a.js")\n',
                {'w/a.js': 'a', 'w/a.css': 'a'})
    plugin_env(b, a)                               # 扫描顺序 beta 在前, 注入要按名字排序
    html = client.get('/').text
    head, sep, tail = html.rpartition('</body>')
    assert sep and tail == '</html>'
    assert head.startswith(PAGE[:PAGE.rindex('</body>')])
    block = head[PAGE.rindex('</body>'):]
    lines = [ln for ln in block.splitlines() if ln.startswith('<')]
    assert lines[0] == '<!-- plugin web assets -->'
    assert [re.search(r'(?:src|href)="([^"?]+)', ln).group(1) for ln in lines[1:]] == [
        '/plugin-assets/alpha/a.css', '/plugin-assets/alpha/a.js', '/plugin-assets/beta/b.js']
    assert lines[1].startswith('<link rel="stylesheet" href="/plugin-assets/alpha/a.css?v=')
    assert lines[1].endswith('data-plugin="alpha">')
    assert lines[2].startswith('<script src="/plugin-assets/alpha/a.js?v=')
    assert lines[2].endswith('defer data-plugin="alpha"></script>')


def test_inject_html_no_body(tmp_path, plugin_env):
    d = _plugin(tmp_path, 'hello', GOOD, GOOD_FILES)
    plugin_env(d)
    out = pa.inject_html('<p>x</p>')
    assert out.startswith('<p>x</p><!-- plugin web assets -->') and out.count('<script') == 1


def test_bad_entries_filtered(tmp_path, plugin_env, client, caplog):
    d = _plugin(tmp_path, 'hello', '''
        WEB_ASSETS = "web"
        WEB_ENTRY = ["ok.js", "../x.js", "sub/y.js", "a.py", "\\"><img src=x onerror=alert(1)>.js",
                     "missing.js", 42, "..js", ".hidden.js"]
    ''', {'web/ok.js': '1', 'web/sub/y.js': '2', 'web/a.py': '3'})
    plugin_env(d)
    with caplog.at_level(logging.WARNING, logger='routers.plugin_assets'):
        html = client.get('/').text
    assert html.count('/plugin-assets/') == 1 and '/plugin-assets/hello/ok.js' in html
    assert 'onerror' not in html and '<img' not in html
    assert 'WEB_ENTRY' in caplog.text


@pytest.mark.parametrize('body', [
    'WEB_ASSETS = "no_such_dir"\nWEB_ENTRY = ["a.js"]\n',
    'WEB_ASSETS = 123\nWEB_ENTRY = ["a.js"]\n',
    'WEB_ASSETS = ""\n',
    'WEB_ASSETS = "plugin.py"\nWEB_ENTRY = ["a.js"]\n',
    'WEB_ASSETS = "web"\nWEB_ENTRY = "a.js"\n',
    'WEB_ENTRY = ["a.js"]\n',
])
def test_bad_plugin_skipped_good_kept(tmp_path, plugin_env, client, body):
    bad = _plugin(tmp_path, 'bad', body, {'web/a.js': 'bad'})
    good = _plugin(tmp_path, 'good', 'WEB_ASSETS = "web"\nWEB_ENTRY = ["g.js"]\n', {'web/g.js': 'g'})
    plugin_env(bad, good)
    html = client.get('/').text
    assert '/plugin-assets/good/g.js' in html
    assert '/plugin-assets/bad/' not in html
    assert client.get('/plugin-assets/good/g.js').text == 'g'


def test_unsafe_plugin_name_skipped(tmp_path, plugin_env, client):
    d = _plugin(tmp_path, 'bad name"x', GOOD, GOOD_FILES)
    plugin_env(d)
    assert pa.collect_web_plugins() == {}
    assert client.get('/').content == PAGE.encode()


def test_multi_plugin_dir_naming(tmp_path, plugin_env, client):
    root = tmp_path / 'suite'
    (root / 'plugins' / 'web').mkdir(parents=True)
    (root / 'plugins' / 'web' / 'm.js').write_text('m')
    (root / 'plugins' / 'mod.py').write_text('WEB_ASSETS = "web"\nWEB_ENTRY = ["m.js"]\n')
    plugin_env(root)
    assert client.get('/plugin-assets/suite.mod/m.js').text == 'm'
    assert '/plugin-assets/suite.mod/m.js' in client.get('/').text


def test_router_auto_discovered_before_static():
    found = routers.discover(include_plugins=False)
    assert pa.router in found and found.index(pa.router) < found.index(static_router.router)


def test_web_ui_html_references_plugin_panels():
    html = (ROOT / 'web_ui.html').read_text(encoding='utf-8')
    assert html.index('/assets/sidebar_groups.js') < html.index('/assets/plugin_panels.js')
    js = (ROOT / 'web_assets' / 'plugin_panels.js').read_text(encoding='utf-8')
    assert 'registerPanel' in js and len(js.splitlines()) <= 200
