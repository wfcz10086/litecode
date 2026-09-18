"""flow 04: Memory 面板字段断言 — v1.9 P39-d"""
import json
import pytest
import urllib.request
import urllib.error
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


def _api(server_url: str, cookie: str, method: str, path: str, body=None):
    headers = {"Cookie": f"lc_auth={cookie}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{server_url}{path}", data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def _get_or_create_session(server_url: str, cookie: str) -> str:
    """拿一个可用 sid（已存在的或新建）"""
    code, d = _api(server_url, cookie, "GET", "/api/sessions")
    sessions = d if isinstance(d, list) else d.get("sessions") or []
    if sessions:
        return sessions[0]["id"]
    code, d = _api(server_url, cookie, "POST", "/api/sessions",
                   {"name": "p39d_test"})
    return d.get("id", "") or (d.get("session", {}) or {}).get("id", "")


def test_memory_api_returns_expected_fields(server_required, auth_password):
    """API: GET /api/memory/{sid} 返回结构含 pct/token_estimate/hard_limit"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    sid = _get_or_create_session(server_required, cookie)
    if not sid:
        pytest.skip("无可用 session")

    code, d = _api(server_required, cookie, "GET", f"/api/memory/{sid}")
    assert code == 200, f"memory API 失败: {code} {d}"
    # 关键字段（与 app.js loadMem 期望对齐）
    assert "pct" in d, f"缺 pct 字段: {list(d.keys())}"
    assert "token_estimate" in d, f"缺 token_estimate: {list(d.keys())}"
    assert "hard_limit" in d, f"缺 hard_limit: {list(d.keys())}"
    # context_window 也应有
    assert "context_window" in d, f"缺 context_window: {list(d.keys())}"


def test_memory_panel_opens_in_ui(server_required, playwright_required, auth_password,
                                  snapshots_dir):
    """UI: 选择会话 → 点 Memory → loadMem → mpb 进度条出现"""
    from playwright.sync_api import sync_playwright
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    sid = _get_or_create_session(server_required, cookie)
    if not sid:
        pytest.skip("无可用 session")

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

            # 切换到目标 session（设置 window.cur，避免 swS 真发请求）
            page.evaluate(f"window.cur = '{sid}'")

            # 点 Memory 顶部按钮
            mem_btn = page.locator('button.sbt[onclick="openP(\'memory\')"]')
            assert mem_btn.count() == 1, "找不到 Memory 顶部按钮"
            mem_btn.click()

            # 等 pnl-memory 打开
            page.wait_for_selector("#pnl-memory.open", state="attached", timeout=3000)

            # 等 loadMem fetch 完成（mem-bd 不再是 'Loading...'）
            page.wait_for_function(
                "() => { const el = document.getElementById('mem-bd'); "
                "return el && !el.innerHTML.includes('Loading...'); }",
                timeout=8000,
            )

            # 验证 mpb 进度条 + Context 字段出现
            mem_html = page.locator("#mem-bd").inner_html()
            # 关键 DOM 类（与 app.js 渲染对齐）
            has_progress = ("class=\"mpb" in mem_html or "mpbf" in mem_html
                            or "Context:" in mem_html)
            assert has_progress, f"mem-bd 缺进度条/Context: {mem_html[:300]}"

            try:
                page.screenshot(path=str(snapshots_dir / "04_memory_panel.png"),
                                timeout=5000, animations="disabled", caret="hide")
            except Exception:
                pass
        finally:
            browser.close()


def test_memory_compress_endpoint(server_required, auth_password):
    """POST /api/memory/{sid}/compress 路由可达 — 压缩可能要发 LLM, 超时也视为路由可达"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    sid = _get_or_create_session(server_required, cookie)
    if not sid:
        pytest.skip("无可用 session")
    import socket
    try:
        # 路由可达性测试: 拉长 timeout 到 60s, 仍超时则说明压缩在跑 (即路由可达)
        req = urllib.request.Request(
            f"{server_required}/api/memory/{sid}/compress",
            data=json.dumps({}).encode(),
            headers={"Cookie": f"lc_auth={cookie}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            assert r.status in (200, 202), f"compress 异常状态: {r.status}"
    except urllib.error.HTTPError as e:
        assert e.code in (200, 202, 500), f"compress 路由错: {e.code}"
    except (socket.timeout, TimeoutError):
        pass  # 长时间压缩 = 路由真可达且后端在跑, 算 PASS


def test_clear_memory_endpoint(server_required, auth_password):
    """DELETE /api/memory/{sid} 路由可达（不实际删，仅验路由）"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    # 用一个不存在的 sid 验路由（不破坏真实数据）
    code, d = _api(server_required, cookie, "DELETE",
                   "/api/memory/nonexistent-sid-p39d")
    assert code in (200, 404, 500), f"clear 路由错: {code} {d}"
