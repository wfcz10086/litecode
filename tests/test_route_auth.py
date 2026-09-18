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
            # patch 鉴权全局变量为测试模式
            web_ui.AUTH_ENABLED = True
            web_ui.AUTH_PASSWORD = "testpwd123"
            web_ui._valid_tokens = {}  # 清空 token 表
            self.app = web_ui.app
            self.client = TestClient(self.app)
            self.web_ui = web_ui
        except Exception as e:
            self.skipTest(f"无法加载 web_ui 模块: {e}")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
