"""修改 Web 登录密码 (POST /api/auth/password) + /api/config 不能绕过.

全部跑在临时 config.json / 临时 token 文件 / 临时 audit.log 上, 绝不碰生产 config.json.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.auth import core as auth          # noqa: E402
from routers import config_router              # noqa: E402
from routers.state import S                    # noqa: E402
import lib.audit as audit_mod                  # noqa: E402

OLD = "old-pass-123"
NEW = "brand-new-pass-456"
REAL_CFG = Path("/opt/litecode/config.json")


def _cfg_doc():
    return {
        "server": {"host": "0.0.0.0", "port": 1, "token": "srv-token-xyz"},
        "model": {"id": "m", "api_key": "sk-EXAMPLE"},
        "web_ui": {"port": 18790, "title": "中文标题",
                   "auth": {"enabled": True, "password": OLD, "session_days": 7}},
        "agent": {"task_timeout_seconds": 1800},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    real_before = REAL_CFG.read_bytes() if REAL_CFG.exists() else None
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(_cfg_doc(), ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    monkeypatch.setattr(S, "cfg_path", cfg_file, raising=False)
    monkeypatch.setattr(auth, "_CFG_PATH", cfg_file)
    monkeypatch.setattr(auth, "_TOKENS_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(auth, "_valid_tokens", {})
    monkeypatch.setattr(auth, "_login_fails", {})
    monkeypatch.setattr(auth, "_login_lock_until", {})
    monkeypatch.setattr(auth, "_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_AUTH_PASSWORD", OLD)
    monkeypatch.setattr(audit_mod, "_AUDIT_DIR", tmp_path)
    monkeypatch.setattr(audit_mod, "_AUDIT_FILE", tmp_path / "audit.log")
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(config_router.router)
    yield {"app": app, "cfg": cfg_file, "tmp": tmp_path}
    if real_before is not None:
        assert REAL_CFG.read_bytes() == real_before, "生产 config.json 被改动了!"


def _login(app, pwd):
    c = TestClient(app)
    r = c.post("/api/login", json={"password": pwd})
    return c, r


def _change(c, old, new):
    return c.post("/api/auth/password", json={"old_password": old, "new_password": new})


def test_requires_auth(env):
    c = TestClient(env["app"])
    assert _change(c, OLD, NEW).status_code == 401


def test_wrong_old_password_403_and_rate_limited(env):
    c, r = _login(env["app"], OLD)
    assert r.status_code == 200
    for _ in range(auth._RATE_MAX_FAILS):
        assert _change(c, "nope-nope", NEW).status_code == 403
    # 达阈值后被锁, 连正确旧密码也 429
    assert _change(c, OLD, NEW).status_code == 429
    # 与登录共用限流
    assert TestClient(env["app"]).post("/api/login", json={"password": OLD}).status_code == 429
    assert json.loads(env["cfg"].read_text())["web_ui"]["auth"]["password"] == OLD
    log = (env["tmp"] / "audit.log").read_text()
    assert "password_change_failed" in log and "nope-nope" not in log


@pytest.mark.parametrize("bad", ["", "short7!", "CHANGE_ME", "change_me", OLD, " padded-pass ", 123, None])
def test_weak_new_password_400(env, bad):
    c, _ = _login(env["app"], OLD)
    before = env["cfg"].read_bytes()
    r = c.post("/api/auth/password", json={"old_password": OLD, "new_password": bad})
    assert r.status_code == 400, (bad, r.text)
    assert env["cfg"].read_bytes() == before


def test_success_flow(env):
    app = env["app"]
    before = json.loads(env["cfg"].read_text())
    other, r = _login(app, OLD)
    assert r.status_code == 200
    me, _ = _login(app, OLD)
    other_tok = other.cookies.get(auth._COOKIE_NAME)
    my_tok = me.cookies.get(auth._COOKIE_NAME)
    assert other_tok and my_tok and other_tok != my_tok

    r = _change(me, OLD, NEW)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["revoked"] == 1

    # 当前会话仍有效, 其它会话失效
    assert me.get("/api/auth/status").json()["ok"] is True
    assert me.get("/api/config").status_code == 200
    assert other.get("/api/config").status_code == 401
    assert my_tok in auth._valid_tokens and other_tok not in auth._valid_tokens
    assert list(json.loads((env["tmp"] / "tokens.json").read_text())) == [my_tok]

    # 立即生效: 新密码可登录, 旧密码不行 (无需重启)
    assert _login(app, NEW)[1].status_code == 200
    assert _login(app, OLD)[1].status_code == 403

    # 文件: 只有密码变了, 缩进 4 + 末尾换行 + 中文不转义
    text = env["cfg"].read_text(encoding="utf-8")
    after = json.loads(text)
    assert after["web_ui"]["auth"]["password"] == NEW
    after["web_ui"]["auth"]["password"] = OLD
    assert after == before
    assert text.endswith("\n") and '\n    "server": {' in text and "中文标题" in text

    log = (env["tmp"] / "audit.log").read_text()
    assert "auth.password_change" in log and NEW not in log and OLD not in log


def test_init_cfg_path_and_reinit_reads_new_password(env, monkeypatch):
    c, _ = _login(env["app"], OLD)
    assert _change(c, OLD, NEW).status_code == 200
    # 模拟重启: 从文件重新 init, 密码仍是新的
    auth.init(json.loads(env["cfg"].read_text()), cfg_path=env["cfg"])
    assert auth._AUTH_PASSWORD == NEW


def test_config_patch_cannot_change_password(env):
    c, _ = _login(env["app"], OLD)
    r = c.patch("/api/config", json={"web_ui": {"auth": {"enabled": True, "password": "hacked-pass-1"}}})
    assert r.status_code == 400 and "/api/auth/password" in r.json()["detail"]
    assert json.loads(env["cfg"].read_text())["web_ui"]["auth"]["password"] == OLD

    # 脱敏占位符 / 不带 password → 原密码保留, 其它字段照常合并
    r = c.patch("/api/config", json={"web_ui": {"auth": {"password": "••••••••", "session_days": 3}}})
    assert r.status_code == 200, r.text
    r = c.patch("/api/config", json={"web_ui": {"auth": {"session_days": 5}, "title": "x"}})
    assert r.status_code == 200, r.text
    a = json.loads(env["cfg"].read_text())["web_ui"]["auth"]
    assert a == {"enabled": True, "password": OLD, "session_days": 5}
    # 密码没变, 旧密码仍可登录
    assert _login(env["app"], OLD)[1].status_code == 200


def test_login_non_ascii_password_no_500(env):
    c = TestClient(env["app"])
    assert c.post("/api/login", json={"password": "密码错误"}).status_code == 403


def test_cli_passwd_calls_endpoint(monkeypatch):
    from click.testing import CliRunner
    from lib.cli import cmd_auth
    calls = []

    def fake(method, path, **kw):
        calls.append((method, path, kw.get("json_body")))
        return {"ok": True, "revoked": 2}
    monkeypatch.setattr(cmd_auth, "_r_json", fake)
    r = CliRunner().invoke(cmd_auth.passwd, ["--old", OLD, "--new", NEW])
    assert r.exit_code == 0, r.output
    assert calls == [("POST", "/api/auth/password", {"old_password": OLD, "new_password": NEW})]
