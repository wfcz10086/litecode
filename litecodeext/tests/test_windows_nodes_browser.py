"""浏览器验证节点入口、命令、回传截图和撤销交互。"""
import glob
import json
from pathlib import Path
import pytest


def test_enrollment_panel_and_revocation():
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
            nodes = []
            def route(request):
                method = request.request.method
                if method == 'POST' and request.request.url.endswith('/uninstall'):
                    nodes[0]['state'] = 'uninstall_pending'
                    result = {'state': 'uninstall_pending'}
                elif method == 'POST' and request.request.url.endswith('/remove'):
                    nodes.clear()
                    result = {'ok': True}
                elif method == 'POST':
                    data = request.request.post_data_json
                    assert data['server_url'] == 'https://node.test'
                    nodes.append({'id': 'test', 'alias': data['alias'], 'state': 'online',
                                  'desktop_state': 'desktop_ready', 'screenshot': '/uploads/test.png',
                                  'update_state': 'legacy',
                                  'error': '<img src=x onerror=alert(1)>'})
                    result = {'command': 'powershell.exe -EncodedCommand TEST'}
                elif method == 'DELETE':
                    nodes[0]['state'] = 'revoked'
                    result = {'ok': True}
                elif request.request.url.endswith('/update-command'):
                    result = {'command': 'powershell.exe -EncodedCommand UPDATE'}
                elif request.request.url.endswith('/uninstall-command'):
                    result = {'command': 'powershell.exe -EncodedCommand UNINSTALL'}
                else:
                    result = {'nodes': nodes}
                request.fulfill(content_type='application/json', body=json.dumps(result))
            page.route('https://node.test/api/windows/nodes**', route)
            page.route('https://node.test/', lambda r: r.fulfill(body='<html><body></body></html>', content_type='text/html'))
            page.goto('https://node.test/')
            page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'web_assets/windows_nodes.js'))
            page.evaluate('openWindowsNodes()')
            page.get_by_placeholder('节点别名，例如 win-office').fill('win-new')
            page.get_by_role('button', name='生成接入命令').click()
            page.wait_for_function('document.querySelector("textarea").value.includes("EncodedCommand")')
            assert page.get_by_text('win-new · 在线', exact=True).count() == 1
            assert page.get_by_role('link', name='查看最近截图').get_attribute('href').endswith('%2Fuploads%2Ftest.png')
            assert page.locator('dialog img').count() == 1
            assert page.locator('dialog img[onerror]').count() == 0
            # 旧代理: 显示需要本机更新, 更新命令可取出
            page.get_by_text('旧版代理', exact=False).wait_for()
            page.get_by_role('button', name='本机更新命令', exact=True).click()
            page.wait_for_function('document.querySelector("textarea").value.includes("UPDATE")')
            nodes[0].update(update_state='current', agent_version='0123456789abcdef', assets_version='fedcba9876543210')
            page.get_by_text('代理 01234567 · 组件 fedcba98 · 已是最新', exact=False).wait_for(timeout=5000)
            assert page.get_by_role('button', name='本机更新命令', exact=True).count() == 0
            page.get_by_role('button', name='本机卸载命令', exact=True).click()
            page.wait_for_function('document.querySelector("textarea").value.includes("UNINSTALL")')
            page.get_by_role('button', name='完全卸载', exact=True).click()
            page.get_by_text('win-new · 等待电脑接收卸载', exact=True).wait_for()
            assert page.get_by_text('win-new · 已完全卸载', exact=True).count() == 0
            # 只有收到服务端确认后的状态，页面才显示卸载完成。
            nodes[0]['state'] = 'uninstalled'
            page.get_by_text('win-new · 已完全卸载', exact=True).wait_for(timeout=5000)
            assert page.get_by_role('button', name='完全卸载', exact=True).count() == 0
            # 同时验证另一条授权撤销路径。
            nodes[0]['state'] = 'online'
            page.get_by_role('button', name='撤销接入').wait_for(timeout=5000)
            page.get_by_role('button', name='撤销接入').click()
            page.get_by_text('win-new · 已撤销', exact=True).wait_for()
            # 已结束节点可删除记录：第一下只进入确认态，跨过 3 秒刷新仍保持，第二下才真正删除。
            page.get_by_role('button', name='删除记录').click()
            page.wait_for_timeout(3500)
            assert nodes and page.get_by_role('button', name='确认删除').count() == 1
            page.get_by_role('button', name='确认删除').click()
            page.get_by_text('尚未登记 Windows 节点。').wait_for(timeout=5000)
            page.get_by_role('button', name='关闭', exact=True).click()
            page.wait_for_function('document.querySelector("textarea").value === ""')
        finally:
            browser.close()
