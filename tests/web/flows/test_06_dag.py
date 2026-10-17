"""flow 06: DAG 编辑器 E2E — v1.9 P39-f (验证 v1.8 P34-d 新增 UI)"""
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


def test_dag_list_api(server_required, auth_password):
    """T1: GET /api/dags 列表路由可达"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    code, d = _api(server_required, cookie, "GET", "/api/dags")
    assert code == 200, f"GET /api/dags 失败: {code} {d}"
    assert "dags" in d or isinstance(d, (list, dict))


def test_dag_put_then_get(server_required, auth_password):
    """T2: PUT /api/dags/{name} 持久化 + GET 读回"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    name = "p39f_test_dag"
    plan = {
        "steps": [
            {"id": "n1", "task": "first", "agent_type": "coder", "label": "A"},
            {"id": "n2", "task": "second", "agent_type": "coder", "label": "B",
             "depends_on": ["n1"]},
        ]
    }
    code, d = _api(server_required, cookie, "PUT", f"/api/dags/{name}", plan)
    assert code == 200, f"PUT 失败: {code} {d}"
    # 读回
    code, d = _api(server_required, cookie, "GET", f"/api/dags/{name}")
    assert code == 200, f"GET 失败: {code} {d}"
    # 应能拿到 steps
    plan_back = d.get("plan", d)
    assert "steps" in plan_back, f"返回 plan 缺 steps: {d}"
    assert len(plan_back["steps"]) == 2
    # cleanup
    code, _ = _api(server_required, cookie, "DELETE", f"/api/dags/{name}")
    assert code == 200, "DELETE 失败"


# [2026-10-17] 以下 T3-T5 对齐 dag-v2 (5d2cc17 / P0-6): 旧 #pnl-dag / #dag-canvas /
# openP('dag') / dag_editor.js(dagEditorInit) 已有意移除 (dag_editor.js 收进 _legacy/),
# 生产唯一入口为 openDAGRouter() → openDAG() → dag_ui 全屏 overlay (list/editor/build)。
# 原 T6 (dagEditorInit 画布 API) 随老编辑器整体移除而删除。

def test_dag_button_in_html(server_required):
    """T3: web_ui.html 含 DAG 编排按钮 (openDAGRouter) + dag-v2 四件套 script"""
    with urllib.request.urlopen(f"{server_required}/", timeout=3) as r:
        html = r.read().decode()
    assert 'onclick="openDAGRouter()"' in html, "html 缺 openDAGRouter() DAG 按钮"
    assert "window.openDAGRouter" in html, "html 缺 openDAGRouter 定义"
    for js in ("dag_ui.js", "dag_pipelines.js", "dag_editor_simple.js", "dag_build_view.js"):
        assert f"/assets/{js}" in html, f"html 缺 {js} script src"
    # 老编辑器不应再被引用
    assert "/assets/dag_editor.js" not in html, "html 仍引用已移除的 dag_editor.js"


def test_dag_editor_js_loads(server_required):
    """T4: dag-v2 前端资产可访问且导出入口函数"""
    exports = {
        "dag_ui.js": "window.openDAG",
        "dag_editor_simple.js": "window.mountDAGEditor",
    }
    for js, sym in exports.items():
        with urllib.request.urlopen(f"{server_required}/assets/{js}", timeout=3) as r:
            body = r.read().decode("utf-8", errors="replace")
        assert sym in body, f"{js} 缺 {sym}"


def test_dag_panel_opens_in_ui(server_required, playwright_required, auth_password,
                               snapshots_dir):
    """T5: UI 点 DAG 编排按钮 → #dag-ui 全屏 overlay 打开 + 列表屏激活"""
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
            page.wait_for_function("() => typeof window.openDAG === 'function'",
                                   timeout=5000)

            dag_btn = page.locator('button.sbt[onclick="openDAGRouter()"]')
            assert dag_btn.count() == 1, "找不到 DAG 编排按钮"
            dag_btn.click()

            page.wait_for_selector("#dag-ui.on", state="attached", timeout=3000)
            page.wait_for_selector('#dag-ui .dag-screen[data-screen="list"].on',
                                   state="attached", timeout=3000)

            try:
                page.screenshot(path=str(snapshots_dir / "06_dag_panel.png"),
                                timeout=5000, animations="disabled", caret="hide")
            except Exception:
                pass
        finally:
            browser.close()
