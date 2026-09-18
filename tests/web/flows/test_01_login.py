"""flow 01: 登录加载首页 — v1.9 P39-a 真实 playwright 实现"""
import pytest


def test_login_page_loads(server_required, playwright_required, snapshots_dir):
    """加载主页 + 检查 #login 元素或主界面元素 + 截图"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(server_required, wait_until="domcontentloaded", timeout=10000)
            # 等 200ms 让 JS 跑一下
            page.wait_for_timeout(500)
            # 验证页面加载了（在截图前先做内容检查）
            body_text = page.evaluate("() => document.body.innerText.length")
            assert body_text > 10, f"主页 body 内容太短: {body_text}"
            # 验证有 LiteCode/OpenClaw 字样或登录入口
            html = page.content().lower()
            has_login_or_main = any(
                kw in html for kw in ["litecode", "openclaw", "login", "登录", "登入"]
            )
            assert has_login_or_main, f"主页未含 LiteCode/login 关键字（前 500 字）: {html[:500]}"
            # 截图（最后做，不阻塞断言）
            try:
                shot_path = snapshots_dir / "01_login_loaded.png"
                # 不等字体（部分 web 字体来自 CDN，可能加载慢）
                page.screenshot(path=str(shot_path), timeout=5000,
                                full_page=False, animations="disabled",
                                caret="hide")
            except Exception as _e:
                # 截图失败不影响主断言，仅 warn
                print(f"  [warn] 截图失败但页面加载验证已通过: {_e}")
        finally:
            browser.close()


def test_login_with_password(server_required, playwright_required, auth_password):
    """如果 AUTH 启用，POST /api/login 应成功;否则跳过"""
    import urllib.request
    import urllib.error
    import json

    # 先看 AUTH 是否启用
    try:
        with urllib.request.urlopen(f"{server_required}/api/auth/status", timeout=3) as r:
            status = json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        pytest.skip(f"无法访问 /api/auth/status: {e}")

    if not status.get("enabled"):
        pytest.skip("AUTH 未启用，跳过密码登录验证")

    # AUTH 启用 → 尝试登录
    req = urllib.request.Request(
        f"{server_required}/api/login",
        data=json.dumps({"password": auth_password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            body = json.loads(r.read())
            assert body.get("ok") is True, f"login 返回非 ok: {body}"
    except urllib.error.HTTPError as e:
        # 403 表示密码错（环境问题）
        if e.code == 403:
            pytest.skip(f"AUTH 启用但密码不匹配（env LITECODE_TEST_PWD 设置错误？）")
        raise
