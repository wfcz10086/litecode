"""分享链接的人读页面: 免登录 HTML + 严格 CSP; 内容全部当纯文本渲染 (恶意 HTML 不执行)。"""
import glob
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]


def test_share_page_served_without_auth_with_csp():
    from routers import sessions_router as sr
    app = FastAPI()
    app.include_router(sr.router)
    r = TestClient(app).get('/share/anytoken')
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/html')
    csp = r.headers['content-security-policy']
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp
    assert r.headers['cache-control'] == 'no-store'
    assert '/api/share/' in r.text and 'innerHTML' not in r.text


def test_share_page_renders_messages_as_text():
    playwright = pytest.importorskip('playwright.sync_api')
    page_html = (ROOT / 'web_assets' / 'share.html').read_text()
    payload = {'ok': True, 'expires': 1792300000, 'session': {
        'name': '<b>标题</b>', 'created': 1792200000, 'messages': [
            {'role': 'user', 'content': '你好 <img src=x onerror="window.__pwned=1">'},
            {'role': 'assistant', 'content': '回答', 'tools': ['get_tree: {}']},
            {'role': 'assistant', 'content': ''}]}}
    with playwright.sync_playwright() as p:
        exe = p.chromium.executable_path
        if not Path(exe).exists():
            found = glob.glob('/root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome') + \
                    glob.glob(str(Path.home() / '.cache/ms-playwright/chromium-*/chrome-linux*/chrome'))
            if not found:
                pytest.skip('缺少 Chromium')
            exe = found[0]
        browser = p.chromium.launch(executable_path=exe, headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.route('https://share.test/share/tok1', lambda r: r.fulfill(body=page_html, content_type='text/html'))
            page.route('https://share.test/api/share/tok1', lambda r: r.fulfill(
                body=json.dumps(payload), content_type='application/json'))
            page.goto('https://share.test/share/tok1')
            page.locator('.msg').first.wait_for()
            assert page.locator('.msg').count() == 2                     # 空回复不显示
            assert page.locator('img').count() == 0 and page.evaluate('window.__pwned') is None
            assert 'onerror' in page.locator('.msg.user .content').inner_text()   # 原样当文字显示
            assert page.locator('h1').inner_text() == '<b>标题</b>'
            assert '调用了 1 次工具' in page.locator('.msg.assistant').inner_text()
        finally:
            browser.close()
