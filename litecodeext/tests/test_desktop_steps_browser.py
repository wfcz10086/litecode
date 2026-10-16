"""真实浏览器验收桌面过程卡片：图片加载、失败显示与回放去重。"""
import base64
import glob
from pathlib import Path

import pytest


def test_step_card_images_errors_and_replay():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        executable = p.chromium.executable_path
        if not Path(executable).exists():
            candidates = glob.glob('/root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome')
            if not candidates:
                pytest.skip('缺少 Chromium')
            executable = candidates[0]
        browser = p.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBz8AAAAASUVORK5CYII=')
            page.route('**/api/media?**', lambda route: route.fulfill(content_type='image/png', body=png))
            page.set_content('<base href="http://desktop.test/"><main id="steps"></main>')
            page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'web_assets/desktop_steps.js'))
            step = {'id': 'one', 'ok': False, 'alias': 'win11', 'action': 'ocr', 'state': 'error',
                    'error': '<script>unsafe()</script>', 'ts': 1, 'image': '/tmp/litecode_workspace/uploads/a.png'}
            page.evaluate('(s)=>LCDesktopSteps.render(document.querySelector("main"),s)', step)
            assert page.locator('.desktop-step p').inner_text() == '<script>unsafe()</script>'
            assert page.locator('script').count() == 1  # 只有加载的组件代码，没有错误文本注入。
            page.wait_for_function('document.querySelector("img").naturalWidth === 1')
            step.update(ok=True, state='clicked', error='')
            page.evaluate('(s)=>LCDesktopSteps.render(document.querySelector("main"),s)', step)
            assert page.locator('.desktop-step').count() == 1
            step['id'] = 'two'
            page.evaluate('(s)=>LCDesktopSteps.render(document.querySelector("main"),s)', step)
            assert page.locator('.desktop-step').count() == 2
            assert page.locator('.desktop-step[open]').count() == 1
        finally:
            browser.close()
