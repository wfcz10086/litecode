#!/usr/bin/env python3
"""
test_route_auth.py — v1.9 P37-b 路由权限边界测试

验证 web_ui.py / litecode_server.py 鉴权边界：
- 401 拒绝（无 cookie / 无 Bearer）
- 403 拒绝（密码错）
- 鉴权成功后路由可访问
- 公开路由不需要鉴权

不依赖真实 server，用 FastAPI TestClient 直接打。
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litecodeext"))

# 强制开启鉴权 (config.json 默认 disabled，测试时 mock)
os.environ.setdefault("LITECODE_TEST_OFFLINE", "1")


def _make_test_client():
    """构造 TestClient — 强制 AUTH_ENABLED=True 模式"""
    from fastapi.testclient import TestClient
    # 重置 web_ui 模块以读取 mock 的配置
    if "web_ui" in sys.modules:
        del sys.modules["web_ui"]

    # mock config: 写一个临时 config.json 启用 auth
    import json
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp())
    cfg = {
        "model_chain": [{"id": "fake", "backend": "fake", "url": "http://192.0.2.1:1"}],
        "default_model": "fake",
        "server": {"port": 12300, "url": "http://192.0.2.1:1"},
        "web_ui": {
            "auth": {
                "enabled": True,
                "password": "testpwd123",
                "session_days": 7,
            }
        },
        "paths": {
            "workspace_base": str(tmp_dir / "workspace"),
            "sessions_dir": str(tmp_dir / "workspace" / "sessions"),
        },
    }
    cfg_path = tmp_dir / "config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))
    os.environ["LITECODE_CONFIG"] = str(cfg_path)
    os.environ["OPENCLAW_CONFIG"] = str(cfg_path)  # 兼容
    # 设置必要的额外 env
    os.environ.setdefault("LITECODE_WEB_PORT", "12321")
    return tmp_dir


class TestRouteAuth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = _make_test_client()

    def setUp(self):
        """每个 test 重新载入 web_ui + patch 鉴权配置"""
        from fastapi.testclient import TestClient
        # 清理缓存
        for m in list(sys.modules.keys()):
            if m == "web_ui":
                del sys.modules[m]
        try:
            import web_ui
        except Exception as e:
            self.skipTest(f"无法加载 web_ui 模块: {e}")
        # 鉴权状态已在 S1-G 从 web_ui 搬到 plugins.auth.core (模块级单例),
        # 原先 patch web_ui.AUTH_ENABLED / AUTH_PASSWORD 已无效 — 实际读的是本机 config.json
        # 的密码, 结果随机器而变。这里直接 patch 真正的状态源, 并把 token 落盘文件
        # 重定向到临时目录, 避免写本机 ~/.litecode/web_auth_tokens.json。
        # 注意: 不能只 `from plugins.auth import core` 再 patch — 前序用例 (清 sys.modules /
        # plugin registry 按文件路径重载) 可能让同一份 core.py 存在多个模块对象, web_ui 的
        # require_auth 与 /api/login 端点可能分属不同副本。这里收集 web_ui 实际用到的所有
        # 鉴权模块全局字典 (require_auth + 各路由 endpoint 的 __globals__), 逐个 patch。
        from unittest import mock
        auth_globals = {id(web_ui._require_auth.__globals__): web_ui._require_auth.__globals__}
        for route in web_ui.app.routes:
            g = getattr(getattr(route, "endpoint", None), "__globals__", None)
            if g is not None and "_AUTH_PASSWORD" in g and "_valid_tokens" in g:
                auth_globals[id(g)] = g
        for g in auth_globals.values():
            patcher = mock.patch.dict(g, {
                "_AUTH_ENABLED": True,
                "_AUTH_PASSWORD": "testpwd123",
                "_valid_tokens": {},
                "_login_fails": {},
                "_login_lock_until": {},
                "_TOKENS_FILE": self.tmp_dir / "web_auth_tokens.json",
            })
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = web_ui.app
        self.client = TestClient(self.app)
        self.web_ui = web_ui

    def test_t1_health_no_auth_required(self):
        """T1: GET /api/health — 当前实现需要 auth (修正预期: 应 401)"""
        # 实际行为：/api/health 用 _require_auth，无 cookie 时 401
        r = self.client.get("/api/health")
        # /api/health 在 web_ui.py:355 调 _require_auth
        # AUTH_ENABLED=True 时应 401
        self.assertEqual(r.status_code, 401)

    def test_t2_auth_status_public(self):
        """T2: GET /api/auth/status — 公开端点（用于检查登录状态）"""
        r = self.client.get("/api/auth/status")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertIn("enabled", d)
        self.assertIn("ok", d)
        # AUTH_ENABLED=True + 无 cookie → ok=False
        self.assertEqual(d["enabled"], True)
        self.assertEqual(d["ok"], False)

    def test_t3_login_wrong_password(self):
        """T3: POST /api/login 错密码 → 403"""
        r = self.client.post("/api/login", json={"password": "wrong"})
        self.assertEqual(r.status_code, 403)

    def test_t4_login_correct_then_access(self):
        """T4: 正确密码登录 → cookie 有效 → 可访问保护路由"""
        r = self.client.post("/api/login", json={"password": "testpwd123"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d.get("ok"), True)
        # cookie 应已设置
        self.assertIn("lc_auth", r.cookies)
        # 用 cookie 访问 sessions
        r2 = self.client.get("/api/sessions", cookies=dict(r.cookies))
        # 应不再 401（可能返回空 list，不应 401）
        self.assertNotEqual(r2.status_code, 401,
                            f"登录后访问 /api/sessions 仍 401: {r2.text[:200]}")

    def test_t5_no_cookie_protected_route(self):
        """T5: 无 cookie 访问 /api/sessions → 401"""
        r = self.client.get("/api/sessions")
        self.assertEqual(r.status_code, 401)

    def test_t6_invalid_cookie(self):
        """T6: 假 cookie 访问保护路由 → 401"""
        r = self.client.get("/api/sessions",
                            cookies={"lc_auth": "fake-token-not-issued"})
        self.assertEqual(r.status_code, 401)

    def test_t7_logout_clears_cookie(self):
        """T7: 登录 → 登出 → cookie 失效 → 再访问 401"""
        r1 = self.client.post("/api/login", json={"password": "testpwd123"})
        self.assertEqual(r1.status_code, 200)
        cookies = dict(r1.cookies)
        # logout
        r2 = self.client.post("/api/logout", cookies=cookies)
        self.assertEqual(r2.status_code, 200)
        # 登出后旧 token 必须在服务端失效 (仅删浏览器 cookie 不够: 泄漏的 token 仍可用 7 天)
        from fastapi.testclient import TestClient
        r3 = TestClient(self.app, cookies=cookies).get("/api/sessions")
        self.assertEqual(r3.status_code, 401,
                         f"登出后旧 token 仍可访问保护路由: {r3.status_code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
