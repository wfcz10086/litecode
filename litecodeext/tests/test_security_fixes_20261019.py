"""安全修复回归 (2026-10-19): XSS 净化 / 网关观测接口鉴权 / 分享泄露 / 配置脱敏 / 控制端密钥 /
活动内容沙箱 / 工作树路径 / 路径穿越 / 工具参数脱敏 / token 字面量 / noVNC 鉴权反代。

不连生产服务, 全部用 TestClient + 临时目录。token 字面量检查从 config.json 读值比对, 本文件不含任何密钥。
"""
import asyncio
import json
import os
import re
import stat
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ───────────────────────── 公共: Web 登录态 ─────────────────────────
@pytest.fixture
def web_auth(monkeypatch):
    """打开 Web 登录校验, 只认 cookie lc_auth=goodtok。"""
    from plugins.auth import core as auth
    monkeypatch.setattr(auth, "_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_valid_tokens", {"goodtok": time.time() + 3600})
    return {"lc_auth": "goodtok"}


# ───────────────────────── 1. Markdown 渲染一律过 DOMPurify ─────────────────────────
WEB = ROOT / "web_assets"


def test_all_marked_render_sites_are_sanitized():
    offenders = []
    for js in WEB.glob("*.js"):
        if js.name == "vendor.js":
            continue
        for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bmarked\.(parse|marked)\s*\(|\bmarked\s*\(", line):
                offenders.append(f"{js.name}:{i}")
    assert not offenders, f"直接 marked.parse 进 innerHTML (未净化): {offenders}"
    for name in ("chat_render.js", "chat_stream.js", "chat_msg_actions.js", "canvas.js"):
        assert "lcSafeMd(" in (WEB / name).read_text(encoding="utf-8"), name


def test_dompurify_vendored_and_bundled_like_marked():
    """DOMPurify 本地托管在 vendor/purify.min.js, 并像 marked 一样内联进 vendor.js (主页第一个脚本)。"""
    purify = (WEB / "vendor" / "purify.min.js").read_text(encoding="utf-8").rstrip("\n")
    assert "DOMPurify" in purify[:400]
    vendor = (WEB / "vendor.js").read_text(encoding="utf-8")
    assert purify in vendor                                   # 与 vendor/ 下原文件逐字节一致
    assert "window.lcSafeMd = safeMd" in vendor and "DOMPurify.sanitize" in vendor and "fail closed" in vendor
    assert vendor.index("marked.js v9") < vendor.index("window.lcSafeMd = safeMd")
    html = (ROOT / "web_ui.html").read_text(encoding="utf-8")
    assert html.index("/assets/vendor.js") < html.index("/assets/chat_render.js")
    assert "cdn.jsdelivr.net/npm/dompurify" not in html


def test_safe_md_neutralizes_payload_in_node():
    """node 里加载 vendor.js 的净化出口 (无 DOM 时 DOMPurify 不可用 → 必须 fail closed 成转义文本)。"""
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("no node")
    js = ("global.window=globalThis;global.self=globalThis;require(process.argv[1]);"
          "process.stdout.write(globalThis.lcSafeMd('<img src=x onerror=alert(1)> **b**'))")
    out = subprocess.run([node, "-e", js, str(WEB / "vendor.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "<img" not in out.stdout and "&lt;img" in out.stdout


def test_main_page_has_csp(monkeypatch):
    from routers import static_router
    from routers.state import S
    monkeypatch.setattr(S, "get_html", lambda: "<html><head></head><body>x</body></html>", raising=False)
    app = FastAPI(); app.include_router(static_router.router)
    r = TestClient(app).get("/")
    csp = r.headers.get("content-security-policy", "")
    for d in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'self'", "script-src 'self'"):
        assert d in csp
    assert "http:" not in csp.split("script-src", 1)[1].split(";")[0].replace("https:", "")
    assert r.headers.get("x-content-type-options") == "nosniff"


# ───────────────────────── 2. 网关观测接口需要 Bearer ─────────────────────────
@pytest.fixture
def gateway(monkeypatch):
    from gateway import observability as obs
    fake = types.SimpleNamespace(TOKEN="gw-test-token", MODEL_ID="m-secret", BACKEND_URL="http://10.0.0.9/v1",
                                 BACKEND_TYPE="openai", PORT=1, WORKSPACE="/tmp")
    from gateway import _deps
    monkeypatch.setattr(_deps, "_srv", lambda: fake)
    monkeypatch.setattr(obs, "_srv", lambda: fake)
    monkeypatch.setattr(obs, "_questions_load",
                        lambda: [{"q": f"q{i}", "session": "s"} for i in range(2000)])
    monkeypatch.setattr(obs, "_stats_load", lambda: {})
    app = FastAPI(); app.include_router(obs.router)
    return TestClient(app), {"Authorization": "Bearer gw-test-token"}


@pytest.mark.parametrize("path", ["/v1/questions", "/questions", "/v1/stats", "/stats",
                                  "/v1/clawinfo", "/clawinfo", "/v1/map_status"])
def test_observability_requires_token(gateway, path):
    c, h = gateway
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get(path, headers=h).status_code == 200


def test_questions_limit_capped(gateway):
    c, h = gateway
    assert len(c.get("/v1/questions?limit=0", headers=h).json()["questions"]) == 1
    assert len(c.get("/v1/questions?limit=-5", headers=h).json()["questions"]) == 1
    assert len(c.get("/v1/questions?limit=99999", headers=h).json()["questions"]) == 500


def test_health_public_minimal_and_full_with_token(gateway):
    c, h = gateway
    pub = c.get("/health").json()
    assert pub["status"] == "ok" and "version" in pub
    assert not {"model", "backend_url", "backend_type", "port"} & set(pub)
    full = c.get("/health", headers=h).json()
    assert full["model"] == "m-secret" and full["backend_url"]


def test_empty_gateway_token_fails_closed(monkeypatch):
    from gateway import _deps
    monkeypatch.setattr(_deps, "_srv", lambda: types.SimpleNamespace(TOKEN=""))
    req = types.SimpleNamespace(headers={"Authorization": "Bearer "})
    assert _deps._auth(req) is False


# ───────────────────────── 3. 公开分享只给 role + 脱敏正文 ─────────────────────────
def test_share_public_messages_strip_raw_fields():
    from routers.sessions_router import _share_public_messages
    msgs = [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "key sk-EXAMPLEabcdefghijklmnopqrstuvwxyz"},
        {"role": "assistant", "content": "ok", "reasoning": "secret thoughts",
         "tools": [{"name": "ssh_exec", "args": {"password": "p"}}],
         "_tool_results": [{"content": "/root/.ssh/id_rsa"}], "tool_calls": [{"id": 1}]},
        {"role": "tool", "content": "raw tool output"},
        {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "x"}}]},
    ]
    out = _share_public_messages(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    for m in out:
        assert set(m) == {"role", "content", "tool_count"}
    assert "sk-abcdefghij" not in out[0]["content"]
    assert out[1]["tool_count"] == 1 and out[2]["content"] == "hi"
    blob = json.dumps(out)
    assert "secret thoughts" not in blob and "id_rsa" not in blob and "password" not in blob


def test_share_ttl_capped_30_days(tmp_path, monkeypatch):
    from lib import share
    monkeypatch.setattr(share, "_STORE_DIR", tmp_path)
    monkeypatch.setattr(share, "_STORE_FILE", tmp_path / "share_tokens.json")
    rec = share.create_share("sid", ttl_sec=10 ** 9)
    assert rec["expires"] - rec["created"] <= 30 * 86400 + 1


# ───────────────────────── 4. 配置递归脱敏 ─────────────────────────
def test_config_mask_recursive_and_short_secret_no_suffix():
    from routers.config_router import _mask_sensitive, _restore_placeholders
    cfg = {"server": {"token": "shorttok", "port": 1},
           "model": {"api_key": "sk-EXAMPLE0123456789WXYZ", "max_tokens": 4096},
           "models": [{"api_key": "abc"}],
           "llm_watchdog": {"ssh": {"password": "hunter2!", "key_file": "/root/.ssh/k", "host": "h"}},
           "imagegen": {"providers": {"x": {"api_key_env": "OPENAI_API_KEY", "app_secret": "s3"}}},
           "web_ui": {"auth": {"password": "pw123456", "enabled": True}}}
    m = _mask_sensitive(cfg)
    assert m["server"]["token"] == "••••"                       # 短密钥不露尾巴
    assert m["model"]["api_key"] == "••••WXYZ"                  # 长密钥露后 4 位
    assert m["models"][0]["api_key"] == "••••"
    assert m["llm_watchdog"]["ssh"]["password"] == "••••"
    assert m["llm_watchdog"]["ssh"]["key_file"] == "/root/.ssh/k"
    assert m["imagegen"]["providers"]["x"] == {"api_key_env": "OPENAI_API_KEY", "app_secret": "••••"}
    assert m["web_ui"]["auth"]["password"] == "••••" and m["model"]["max_tokens"] == 4096
    for secret in ("shorttok", "hunter2", "pw123456", "0123456789abcdef"):
        assert secret not in json.dumps(m, ensure_ascii=False)
    # PATCH 回写整段 (含嵌套占位符) 不覆盖真值
    back = _restore_placeholders(m["llm_watchdog"], cfg["llm_watchdog"])
    assert back["ssh"]["password"] == "hunter2!"


def test_config_patch_keeps_nested_secret(tmp_path, monkeypatch, web_auth):
    from routers import config_router
    from routers.state import S
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"agent": {"x": 1, "ssh": {"password": "realpw", "host": "a"}}}))
    monkeypatch.setattr(S, "cfg_path", cfgp, raising=False)
    app = FastAPI(); app.include_router(config_router.router)
    c = TestClient(app, cookies=web_auth)
    got = c.get("/api/config").json()["config"]
    assert got["agent"]["ssh"]["password"] == "••••"
    got["agent"]["ssh"]["host"] = "b"
    assert c.patch("/api/config", json={"agent": got["agent"]}).status_code == 200
    saved = json.loads(cfgp.read_text())
    assert saved["agent"]["ssh"] == {"password": "realpw", "host": "b"}


# ───────────────────────── 5. 控制端密钥移出工作区 + shell 拒绝 ─────────────────────────
def test_key_store_migrates_legacy_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("LITECODE_KEY_DIR", str(tmp_path / "keys"))
    from lib import key_store
    ws = tmp_path / "ws"
    legacy = ws / ".secrets" / "windows_nodes"
    legacy.mkdir(parents=True)
    (legacy / "signing_key.pem").write_bytes(b"PEM-BYTES")
    (legacy / "capability.key.bin").write_bytes(b"\x00LCCAPK1" + b"k" * 32)
    d = key_store.windows_key_dir(ws)
    assert ws not in d.parents
    assert (d / "signing_key.pem").read_bytes() == b"PEM-BYTES"
    assert (d / "capability.key.bin").read_bytes().startswith(b"\x00LCCAPK1")
    assert not (legacy / "signing_key.pem").exists() and not (legacy / "capability.key.bin").exists()
    assert stat.S_IMODE((d / "signing_key.pem").stat().st_mode) == 0o600
    assert key_store.windows_key_dir(ws) == d                 # 幂等


@pytest.mark.parametrize("cmd", ["cat /tmp/litecode_workspace/.secrets/windows_nodes/nodes.json",
                                 "ls ~/.litecode/keys", "cp x/signing_key.pem /tmp/",
                                 "xxd capability.key.bin", "tar cf a.tar litecode_state/keys"])
def test_shell_refuses_secret_paths(cmd):
    import executor_v4 as ex
    out = asyncio.run(ex.execute_shell_async(cmd, workspace=Path("/tmp")))
    assert out.startswith("ERROR[PERMANENT]") and "受保护" in out


def test_shell_normal_command_still_runs():
    import executor_v4 as ex
    assert "hello" in asyncio.run(ex.execute_shell_async("echo hello", workspace=Path("/tmp")))


# ───────────────────────── 6. SVG/HTML 活动内容沙箱 ─────────────────────────
def test_media_and_preview_sandbox_active_content(tmp_path, monkeypatch, web_auth):
    from routers import workspace_router as wr
    from routers.state import S
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')
    (ws / "b.png").write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(S, "workspace", ws, raising=False)
    monkeypatch.setattr(wr, "_WX_SAFE_DIRS", [ws])
    app = FastAPI(); app.include_router(wr.router)
    c = TestClient(app, cookies=web_auth)
    for url in (f"/api/media?path={ws / 'a.svg'}", "/api/workspace/preview?path=a.svg"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-security-policy"].startswith("sandbox"), url
        assert r.headers["x-content-type-options"] == "nosniff"
    r = c.get(f"/api/media?path={ws / 'b.png'}")
    assert "content-security-policy" not in r.headers and r.headers["x-content-type-options"] == "nosniff"


def test_artifact_blob_html_sandboxed(monkeypatch, web_auth):
    from routers import artifacts_v2_router as ar
    meta = types.SimpleNamespace(kind="file", name="r.html")
    store = types.SimpleNamespace(meta=lambda aid: meta, blob=lambda aid: b"<script>x</script>")
    monkeypatch.setattr(ar, "get_store", lambda: store)
    app = FastAPI(); app.include_router(ar.router)
    r = TestClient(app, cookies=web_auth).get("/api/artifacts_v2/blob/a1")
    assert r.headers["content-security-policy"].startswith("sandbox")
    assert "allow-same-origin" not in r.headers["content-security-policy"]


# ───────────────────────── 7. 工作树 id / 路径校验 ─────────────────────────
def test_worktree_ids_and_rmtree_guard(tmp_path, monkeypatch):
    from lib.agent import subagent_worktree as wt
    monkeypatch.setattr(wt, "WORKSPACE_OVERRIDE", tmp_path / "ws")
    for bad in ("../x", "..", "a/b", "-rf", "", "a" * 100, "x\n"):
        assert not wt.valid_wid(bad) and wt.load_meta(bad) is None
    assert wt.valid_wid("coder-1a2b3c4d")
    victim = tmp_path / "victim"; victim.mkdir(); (victim / "f").write_text("keep")
    calls = []
    monkeypatch.setattr(wt, "_git", lambda args, cwd, timeout=0: calls.append(args) or
                        types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    wt._remove_worktree(str(tmp_path), str(victim), "master")
    assert (victim / "f").exists()                                  # 不 rmtree 工作树目录外
    assert not any(a[:2] == ["branch", "-D"] for a in calls)         # 非 lc-agent/ 分支不删
    assert not any(a[:2] == ["worktree", "remove"] for a in calls)
    assert "非法工作树 id" in asyncio.run(wt.agent_worktree_tool({"action": "discard", "id": "../../x"}))


# ───────────────────────── 8. 路径穿越 ─────────────────────────
def test_session_file_traversal_blocked(tmp_path, monkeypatch, web_auth):
    from routers import sessions_router as sr
    from routers.state import S
    sess = tmp_path / "sessions"; (sess / "s1").mkdir(parents=True)
    monkeypatch.setattr(S, "cfg", {"paths": {"sessions_dir": str(sess)}}, raising=False)
    monkeypatch.setattr(sr, "_wui", lambda: types.SimpleNamespace(load_session=lambda sid: {}))
    monkeypatch.setattr(sr, "require_owner", lambda d, r: None)
    app = FastAPI(); app.include_router(sr.router)
    c = TestClient(app, cookies=web_auth)
    name = next(iter(sr._EDITABLE_MDS))
    r = c.put(f"/api/sessions/s1/files/..%2F..%2Fescape%2F{name}", json={"content": "pwn"})
    assert r.status_code == 403
    assert not (tmp_path / "escape").exists()
    with pytest.raises(Exception):
        sr._session_file_path("..", name)
    assert c.put(f"/api/sessions/s1/files/{name}", json={"content": "ok"}).status_code == 200
    assert (sess / "s1" / name).read_text() == "ok"


def test_workspace_and_media_prefix_bug(tmp_path, monkeypatch, web_auth):
    from routers import workspace_router as wr
    from routers.state import S
    ws = tmp_path / "ws"; ws.mkdir()
    sib = tmp_path / "ws2"; sib.mkdir(); (sib / "leak.png").write_bytes(b"x")
    (ws / ".secrets").mkdir(); (ws / ".secrets" / "k.png").write_bytes(b"x")
    monkeypatch.setattr(S, "workspace", ws, raising=False)
    monkeypatch.setattr(wr, "_WX_SAFE_DIRS", [ws])
    app = FastAPI(); app.include_router(wr.router)
    c = TestClient(app, cookies=web_auth)
    assert c.get(f"/api/media?path={sib / 'leak.png'}").status_code == 403      # /ws2 不再被 /ws 前缀放行
    assert c.get(f"/api/media?path={ws / '.secrets' / 'k.png'}").status_code == 403
    assert c.get("/api/workspace/files?path=../ws2").status_code == 403
    assert c.get("/api/workspace/files?path=.secrets").status_code == 403
    assert c.get("/api/workspace/download?path=../ws2/leak.png").status_code == 403
    assert c.get("/api/workspace/files").status_code == 200


# ───────────────────────── 9. 工具参数脱敏 + ssh_hosts 0600 ─────────────────────────
def test_subagent_detail_redacts_secrets():
    from lib.agent import subagent
    red = subagent._redact_args({"host": "h", "password": "p@ss", "opts": {"api_key": "k", "token": "t"}})
    s = str(red)
    assert "p@ss" not in s and "'k'" not in s and "'t'" not in s and "'h'" in s
    src = (ROOT / "lib/agent/subagent.py").read_text(encoding="utf-8")
    assert "str(fn_args)[:80]" not in src


def test_ssh_hosts_file_mode_0600(tmp_path, monkeypatch):
    from core.tools.handlers import ssh_hosts
    p = tmp_path / "sec" / "ssh_hosts.json"
    monkeypatch.setattr(ssh_hosts, "_STORE_PATH", p)
    old = os.umask(0o022)
    try:
        ssh_hosts._save({"a": {"host": "h", "password": "x"}})
    finally:
        os.umask(old)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert json.loads(p.read_text())["a"]["host"] == "h"


# ───────────────────────── 10. 代码/脚本里没有 token 字面量 ─────────────────────────
def test_no_gateway_token_literal_in_code_or_scripts():
    cfg = REPO / "config.json"
    if not cfg.exists():
        cfg = ROOT / "config.json"
    try:
        tok = (json.loads(cfg.read_text()).get("server") or {}).get("token", "")
    except Exception:
        tok = ""
    if not tok or len(tok) < 6:
        pytest.skip("无可比对的 server.token")
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.split()
    files += ["litecodeext/routers/vnc_router.py", "litecodeext/lib/key_store.py"]
    allow = {"config.json", "litecodeext/config.json", "scripts/make_public_release.sh"}  # 发布脱敏清单需要原值
    hits = []
    for f in files:
        if f in allow or f.startswith((".claude/", "_archive/", "dist/", "reports/")):
            continue
        p = REPO / f
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(tok) + r"(?![A-Za-z0-9])", text):
            hits.append(f)
    assert not hits, f"这些文件仍含网关 token 字面量: {hits}"


# ───────────────────────── 11. noVNC 鉴权反代 ─────────────────────────
@pytest.fixture
def vnc_app(tmp_path, monkeypatch):
    from routers import vnc_router
    web = tmp_path / "novnc"; (web / "app").mkdir(parents=True)
    (web / "vnc.html").write_text("<html>noVNC</html>")
    (web / "app" / "ui.js").write_text("export default 1;")
    monkeypatch.setenv("NOVNC_WEB_DIR", str(web))
    app = FastAPI(); app.include_router(vnc_router.router)
    return app


def test_vnc_http_requires_login(vnc_app, web_auth):
    anon = TestClient(vnc_app)
    r = anon.get("/vnc/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert anon.get("/vnc/app/ui.js").status_code == 401
    assert anon.get("/vnc/vnc.html", follow_redirects=False).status_code == 303
    assert anon.get("/vnc/defaults.json").status_code == 401
    c = TestClient(vnc_app, cookies=web_auth)
    r = c.get("/vnc/", follow_redirects=False)
    assert r.status_code == 307 and "path=websockify" in r.headers["location"]
    assert c.get("/vnc/vnc.html").text == "<html>noVNC</html>"
    assert "javascript" in c.get("/vnc/app/ui.js").headers["content-type"]
    assert c.get("/vnc/../../etc/passwd").status_code in (404, 401)
    assert c.get("/vnc/defaults.json").json()["path"] == "websockify"


def test_vnc_websocket_rejects_unauthenticated_and_cross_origin(vnc_app, web_auth):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with TestClient(vnc_app).websocket_connect("/vnc/websockify"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with TestClient(vnc_app, cookies=web_auth).websocket_connect(
                "/vnc/websockify", headers={"Origin": "http://evil.example:45555"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with TestClient(vnc_app).websocket_connect("/websockify"):
            pass


def test_vnc_websocket_relays_when_authenticated(vnc_app, web_auth, monkeypatch):
    """起一个本地 websockets 回显服务冒充 websockify, 验证二进制双向转发 + 子协议透传。"""
    import socket
    import threading
    import websockets
    from websockets.asyncio.server import serve

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    monkeypatch.setenv("NOVNC_PORT", str(port))
    ready, stop = threading.Event(), None
    loop = asyncio.new_event_loop()

    async def echo(conn):
        async for m in conn:
            await conn.send(m)

    async def main():
        nonlocal stop
        stop = asyncio.Event()
        async with serve(echo, "127.0.0.1", port, subprotocols=["binary"]):
            ready.set()
            await stop.wait()

    th = threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True)
    th.start(); assert ready.wait(5)
    try:
        c = TestClient(vnc_app, cookies=web_auth)
        with c.websocket_connect("/vnc/websockify", subprotocols=["binary"],
                                 headers={"Origin": "http://testserver"}) as ws:
            assert ws.accepted_subprotocol == "binary"
            ws.send_bytes(b"\x00RFB")
            assert ws.receive_bytes() == b"\x00RFB"
    finally:
        loop.call_soon_threadsafe(stop.set)
        th.join(5)
