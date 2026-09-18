"""flow 02: 发消息 + 折叠 — v1.9 P39-b 真实 playwright 实现"""
import pytest


def _api_login(server_url: str, password: str) -> str:
    """API 登录，返回 lc_auth cookie 值"""
    import urllib.request
    import json
    req = urllib.request.Request(
        f"{server_url}/api/login",
        data=json.dumps({"password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    cookie = ""
    with urllib.request.urlopen(req, timeout=3) as r:
        for h in r.headers.get_all("Set-Cookie") or []:
            if h.startswith("lc_auth="):
                cookie = h.split(";")[0].split("=", 1)[1]
                break
    return cookie


def _new_page_with_cookie(p, server_url: str, cookie: str):
    """创建新 page 并注入 lc_auth cookie"""
    from urllib.parse import urlparse
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    if cookie:
        u = urlparse(server_url)
        context.add_cookies([{
            "name": "lc_auth", "value": cookie,
            "domain": u.hostname, "path": "/",
        }])
    return browser, context.new_page()


def test_send_button_dom_ready(server_required, playwright_required, auth_password):
    """登录后主页 #mi/#sdb 元素可见（不实际发送，避免依赖 LLM）"""
    from playwright.sync_api import sync_playwright
    try:
        cookie = _api_login(server_required, auth_password)
    except Exception as e:
        pytest.skip(f"登录失败: {e}")

    with sync_playwright() as p:
        browser, page = _new_page_with_cookie(p, server_required, cookie)
        try:
            page.goto(server_required, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(500)
            assert page.locator("#mi").is_visible(), "#mi 输入框不可见"
            assert page.locator("#sdb").is_enabled(), "#sdb 发送按钮不可点"
        finally:
            browser.close()


def test_send_message_input_filled(server_required, playwright_required, auth_password,
                                   snapshots_dir):
    """登录 → 填消息 → 验证 textarea 值更新"""
    from playwright.sync_api import sync_playwright
    try:
        cookie = _api_login(server_required, auth_password)
    except Exception as e:
        pytest.skip(f"登录失败: {e}")

    with sync_playwright() as p:
        browser, page = _new_page_with_cookie(p, server_required, cookie)
        try:
            page.goto(server_required, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(500)
            page.locator("#mi").fill("hi 测试消息")
            value = page.locator("#mi").input_value()
            assert "hi 测试消息" in value, f"#mi 值未更新: {value}"
            # 截图（容错）
            try:
                page.screenshot(path=str(snapshots_dir / "02_input_filled.png"),
                                timeout=5000, animations="disabled", caret="hide")
            except Exception:
                pass
        finally:
            browser.close()


def test_fold_toggle_classes_in_app_js(server_required, playwright_required):
    """验证 web_assets/*.js 含折叠 toggle('open') 关键类（CLI/Web 对齐契约）"""
    import urllib.request
    from pathlib import Path

    web_assets_dir = Path(__file__).resolve().parent.parent.parent.parent / "litecodeext" / "web_assets"
    js = ""
    for js_file in sorted(web_assets_dir.glob("*.js")):
        with urllib.request.urlopen(f"{server_required}/assets/{js_file.name}", timeout=3) as r:
            js += r.read().decode("utf-8", errors="replace")
    # docs/UI_PARITY.md §3.2 列出的折叠 class
    for cls in ["rsh", "targs", "trc", "dth", "lmh"]:
        assert cls in js, f"web_assets/*.js 缺少折叠 class: {cls}"
    assert "classList.toggle('open')" in js, "web_assets/*.js 缺少 toggle('open') 触发"


def test_send_creates_user_msg_row(server_required, playwright_required, auth_password):
    """登录 → 发短消息 → 验证 #msgs 出现 .msg.user（不等 LLM 完整回答）"""
    from playwright.sync_api import sync_playwright
    try:
        cookie = _api_login(server_required, auth_password)
    except Exception as e:
        pytest.skip(f"登录失败: {e}")

    with sync_playwright() as p:
        browser, page = _new_page_with_cookie(p, server_required, cookie)
        try:
            page.goto(server_required, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(800)
            page.locator("#mi").fill("ping")
            page.wait_for_timeout(200)
            page.locator("#sdb").click()
            # 等用户消息渲染（应在毫秒级出现，本地状态先更新）
            try:
                page.wait_for_selector(".msg.user", state="attached", timeout=5000)
                user_msgs = page.locator(".msg.user").count()
                assert user_msgs >= 1, "未出现 .msg.user"
            except Exception:
                # 兜底：从 DOM 检查 ping 字符串
                msgs_text = page.locator("#msgs").inner_text(timeout=3000)
                assert "ping" in msgs_text, f"#msgs 未含 'ping': {msgs_text[:200]}"
        finally:
            browser.close()
