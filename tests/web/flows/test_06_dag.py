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


def test_dag_button_in_html(server_required):
    """T3: web_ui.html 含 🔀 DAG 按钮 + script src dag_editor.js"""
    with urllib.request.urlopen(f"{server_required}/", timeout=3) as r:
        html = r.read().decode()
    # P34-d 加的按钮
    assert "openP('dag')" in html, "html 缺 openP('dag') 按钮"
    assert "DAG" in html, "html 缺 DAG 文字"
    assert "dag_editor.js" in html, "html 缺 dag_editor.js script src"
    # pnl-dag 容器
    assert 'id="pnl-dag"' in html, "html 缺 pnl-dag 容器"
    assert 'id="dag-canvas"' in html, "html 缺 dag-canvas 元素"


def test_dag_editor_js_loads(server_required):
    """T4: /assets/dag_editor.js 可访问 + 含 dagEditorInit 函数"""
    with urllib.request.urlopen(f"{server_required}/assets/dag_editor.js",
                                timeout=3) as r:
        js = r.read().decode("utf-8", errors="replace")
    assert "function dagEditorInit" in js, "缺 dagEditorInit 函数"
    assert "addNode" in js and "addEdge" in js and "toJSON" in js, \
        "dag_editor.js 缺核心 API"


def test_dag_panel_opens_in_ui(server_required, playwright_required, auth_password,
                               snapshots_dir):
    """T5: UI 点 DAG 按钮 → pnl-dag 打开 + dag-canvas 出现 + dag-select 加载"""
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

            # 点 DAG 顶部按钮
            dag_btn = page.locator('button.sbt[onclick="openP(\'dag\')"]')
            assert dag_btn.count() == 1, "找不到 DAG 顶部按钮"
            dag_btn.click()

            # pnl-dag 应打开
            page.wait_for_selector("#pnl-dag.open", state="attached", timeout=3000)

            # 关键 DOM 元素：dag-select / dag-canvas
            assert page.locator("#dag-select").count() == 1, "缺 dag-select"
            assert page.locator("#dag-canvas").count() == 1, "缺 dag-canvas"

            # dag_editor.js 应已加载（dagEditorInit 是全局函数）
            has_init = page.evaluate("() => typeof dagEditorInit === 'function'")
            assert has_init, "dagEditorInit 函数未加载"

            try:
                page.screenshot(path=str(snapshots_dir / "06_dag_panel.png"),
                                timeout=5000, animations="disabled", caret="hide")
            except Exception:
                pass
        finally:
            browser.close()


def test_dag_editor_init_in_canvas(server_required, playwright_required, auth_password):
    """T6: 调用 dagEditorInit('dag-canvas') 后画布可加节点 + toJSON 返回 steps"""
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
            page.wait_for_timeout(500)

            # 打开 DAG 面板（这样 dag-canvas 容器变可见）
            page.evaluate("openP('dag')")
            page.wait_for_timeout(500)

            # 在 page context 中调用 dag editor API
            result = page.evaluate("""() => {
                const ed = dagEditorInit('dag-canvas');
                if (!ed) return {ok: false, reason: 'init 失败'};
                ed.addNode({id: 'a', label: 'A', agent_type: 'coder', x: 50, y: 50});
                ed.addNode({id: 'b', label: 'B', agent_type: 'critic', x: 250, y: 50});
                ed.addEdge('a', 'b');
                const plan = ed.toJSON();
                return {ok: true, plan: plan, node_count: ed._state.nodes.size};
            }""")
            assert result.get("ok"), f"editor 操作失败: {result}"
            assert result["node_count"] == 2, f"节点数错: {result}"
            plan = result["plan"]
            assert "steps" in plan and len(plan["steps"]) == 2, f"plan 错: {plan}"
            # depends_on 应反映 a→b 边
            step_b = next(s for s in plan["steps"] if s["id"] == "b")
            assert "a" in step_b.get("depends_on", []), \
                f"step b 缺 depends_on=a: {step_b}"
        finally:
            browser.close()
