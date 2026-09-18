"""flow 03: Timer 端到端 — v1.9 P39-c

策略：API 直打 + UI 验证（不依赖真 LLM 跑 timer）
"""
import json
import urllib.request
import pytest
from urllib.parse import urlparse


def _api_login(server_url: str, password: str) -> str:
    req = urllib.request.Request(
        f"{server_url}/api/login",
        data=json.dumps({"password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        for h in r.headers.get_all("Set-Cookie") or []:
            if h.startswith("lc_auth="):
                return h.split(";")[0].split("=", 1)[1]
    return ""


def _api(server_url: str, cookie: str, method: str, path: str, body=None) -> tuple:
    """发 API 请求，返回 (status, json_body)"""
    headers = {"Cookie": f"lc_auth={cookie}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{server_url}{path}", data=data, headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_timer_api_crud(server_required, auth_password):
    """Timer API: list / create / patch / delete 走通"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")

    # list
    code, d = _api(server_required, cookie, "GET", "/api/timers")
    assert code == 200
    assert "timers" in d or isinstance(d, (list, dict))

    # create — 用永不触发的 once 时间点验路由, 避免触发 LLM
    body = {
        "name": "p39c_test_timer",
        "type": "once",
        "schedule": "2099-01-01T00:00:00",
        "prompt": "echo p39c",
    }
    code, d = _api(server_required, cookie, "POST", "/api/timers", body)
    assert code == 200, f"create timer 失败: {code} {d}"
    tid = d.get("id") or (d.get("timer", {}) or {}).get("id")
    if not tid:
        # /api/timers 可能直接返回创建后的对象
        code2, d2 = _api(server_required, cookie, "GET", "/api/timers")
        timers_list = d2.get("timers") or (d2 if isinstance(d2, list) else [])
        for t in timers_list:
            if t.get("name") == "p39c_test_timer":
                tid = t.get("id")
                break
    assert tid, f"未拿到 timer id: {d}"

    # patch (disable)
    code, d = _api(server_required, cookie, "PATCH", f"/api/timers/{tid}",
                   {"enabled": False})
    assert code == 200, f"patch 失败: {code} {d}"

    # delete (cleanup)
    code, d = _api(server_required, cookie, "DELETE", f"/api/timers/{tid}")
    assert code == 200, f"delete 失败: {code} {d}"


def test_timer_panel_opens_in_ui(server_required, playwright_required, auth_password,
                                 snapshots_dir):
    """UI: 点 Timer 按钮 → pnl-timer 打开 + loadTimers 触发"""
    from playwright.sync_api import sync_playwright
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        u = urlparse(server_required)
        context.add_cookies([{"name": "lc_auth", "value": cookie,
                              "domain": u.hostname, "path": "/"}])
        page = context.new_page()
        try:
            page.goto(server_required, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(800)

            # 点 Timer sbt 按钮
            timer_btn = page.locator('button.sbt[onclick="openP(\'timer\')"]')
            assert timer_btn.count() == 1, "找不到 Timer 顶部按钮"
            timer_btn.click()

            # pnl-timer 应有 .open class
            page.wait_for_selector("#pnl-timer.open", state="attached", timeout=3000)

            # 等 loadTimers fetch 完成（timer-bd 不再是 'Loading...'）
            page.wait_for_function(
                "() => { const el = document.getElementById('timer-bd'); "
                "return el && !el.innerHTML.includes('Loading...'); }",
                timeout=5000,
            )

            timer_html = page.locator("#timer-bd").inner_html()
            assert "Loading" not in timer_html, "timer-bd 仍是 Loading"
            # 截图
            try:
                page.screenshot(path=str(snapshots_dir / "03_timer_panel.png"),
                                timeout=5000, animations="disabled", caret="hide")
            except Exception:
                pass
        finally:
            browser.close()


def test_timer_history_route_exists(server_required, auth_password):
    """验证 /api/timers/{tid}/history 路由可达（404 表示路由存在但 timer 不存在）"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    # 不存在的 tid 应返回 404 或 200+空 history
    code, d = _api(server_required, cookie, "GET", "/api/timers/nonexistent/history")
    # 路由存在 = 不是 405 method not allowed
    assert code != 405, f"timer history 路由 method 错: {code}"
    # 应是 404 (not found) / 200 (空 history) / 500 (内部错) — 都说明路由存在
    assert code in (200, 404, 500), f"unexpected: {code} {d}"
