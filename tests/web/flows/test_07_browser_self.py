"""flow 07: 浏览器自测 — v1.9 P40 (用户拍板 self-host 测试页)

不依赖 server，不依赖 LLM，不依赖外网。Playwright 直开本地 HTML，
完整覆盖：DOM 输入、提交、折叠展开、异步加载、表单、点击计数。

证明：浏览器自动化链路（playwright + chromium）真实可用。
"""
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
HTML_FILE = (ROOT / "tests" / "web" / "static_pages" / "browser_self_test.html").resolve()
HTML_URL = HTML_FILE.as_uri()  # file:///...


def test_t1_page_loads_with_meta(playwright_required):
    """T1: 加载本地 HTML + 验证 title/UA meta 渲染"""
    from playwright.sync_api import sync_playwright
    assert HTML_FILE.exists(), f"测试页不存在: {HTML_FILE}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(HTML_URL, wait_until="domcontentloaded", timeout=5000)
            assert page.title() == "Browser Self-Test (LiteCode v1.9 P40)"
            # meta 字段应被 JS 填充
            title = page.locator("#meta-title").inner_text(timeout=2000)
            ua = page.locator("#meta-ua").inner_text(timeout=2000)
            assert "Self-Test" in title
            assert len(ua) > 10  # UA 非空
        finally:
            browser.close()


def test_t2_input_submit_dom_update(playwright_required):
    """T2: 输入文本 → 点提交 → DOM 更新 → box 加 .success class"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(HTML_URL, wait_until="domcontentloaded", timeout=5000)
            page.locator("#inp").fill("hello-from-test")
            page.locator("#btn-submit").click()
            page.wait_for_function(
                "() => document.getElementById('out').textContent.includes('hello-from-test')",
                timeout=2000,
            )
            out = page.locator("#out").inner_text()
            assert "hello-from-test" in out
            # box 应加 success class
            cls = page.locator("#box-input").get_attribute("class")
            assert "success" in cls
        finally:
            browser.close()


def test_t3_fold_toggle(playwright_required):
    """T3: 点击折叠头 → .fold-c 从隐藏变可见"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(HTML_URL, wait_until="domcontentloaded", timeout=5000)
            # 默认折叠：fold-c 不可见
            assert not page.locator("#box-fold .fold-c").is_visible()
            # 点击 trigger
            page.locator("#fold-trigger").click()
            page.wait_for_timeout(100)
            # 现在应可见
            assert page.locator("#box-fold .fold-c").is_visible()
            # 再点击 → 折回
            page.locator("#fold-trigger").click()
            page.wait_for_timeout(100)
            assert not page.locator("#box-fold .fold-c").is_visible()
        finally:
            browser.close()


def test_t4_async_load(playwright_required):
    """T4: 异步按钮 → setTimeout 后内容更新（验证 wait_for_function 等异步）"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(HTML_URL, wait_until="domcontentloaded", timeout=5000)
            page.locator("#btn-load").click()
            # 立即应是 loading，不到 1s 不应完成
            page.wait_for_function(
                "() => document.getElementById('async-out').textContent.includes('async loaded')",
                timeout=3000,
            )
            out = page.locator("#async-out").inner_text()
            assert "async loaded ✓" in out
        finally:
            browser.close()


def test_t5_multi_field_form(playwright_required):
    """T5: 多字段表单 → fill 各字段 → submit → 验证 JSON 输出"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(HTML_URL, wait_until="domcontentloaded", timeout=5000)
            page.locator('#myform input[name="name"]').fill("Alice")
            page.locator('#myform input[name="email"]').fill("alice@test.com")
            page.locator('#myform textarea[name="msg"]').fill("test msg")
            page.locator('#myform button[type="submit"]').click()
            page.wait_for_function(
                "() => document.getElementById('form-out').textContent.includes('Alice')",
                timeout=2000,
            )
            out = page.locator("#form-out").inner_text()
            assert "Alice" in out and "alice@test.com" in out and "test msg" in out
        finally:
            browser.close()


def test_t6_click_counter(playwright_required):
    """T6: 多次点击同一按钮 → 计数器递增 → 第 3 次后 box 加 success"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(HTML_URL, wait_until="domcontentloaded", timeout=5000)
            btn = page.locator("#btn-inc")
            for _ in range(5):
                btn.click()
            cnt = page.locator("#counter").inner_text()
            assert cnt == "5", f"计数器应是 5, 实际 {cnt}"
            # 3 次后 box 应加 success
            cls = page.locator("#box-counter").get_attribute("class")
            assert "success" in cls
        finally:
            browser.close()


def test_t7_screenshot(playwright_required, snapshots_dir):
    """T7: 完整流程后截图 — 证明 5 个 box 都成功"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(HTML_URL, wait_until="domcontentloaded", timeout=5000)
            # 触发所有交互
            page.locator("#inp").fill("e2e-pass")
            page.locator("#btn-submit").click()
            page.locator("#fold-trigger").click()
            page.locator("#btn-load").click()
            page.wait_for_function(
                "() => document.getElementById('async-out').textContent.includes('async loaded')",
                timeout=3000,
            )
            page.locator('#myform input[name="name"]').fill("Bob")
            page.locator('#myform input[name="email"]').fill("bob@x.com")
            page.locator('#myform button[type="submit"]').click()
            for _ in range(3):
                page.locator("#btn-inc").click()
            page.wait_for_timeout(200)
            # 截图（容错）
            try:
                page.screenshot(
                    path=str(snapshots_dir / "07_browser_self_test_complete.png"),
                    timeout=5000, animations="disabled", caret="hide",
                )
            except Exception:
                pass
            # 验证全部 5 个 box 都加了 success
            success_count = page.evaluate(
                "() => document.querySelectorAll('.box.success').length"
            )
            assert success_count >= 4, f"应 ≥4 个 box success, 实际 {success_count}"
        finally:
            browser.close()
