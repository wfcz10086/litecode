"""对话中切换模型 (会话级) + 测试指定模型是否可用 [per-chat-model 2026-10-19]。

全部离线: 上游 HTTP 用 httpx.MockTransport 截获, config.json / 会话模型存储都换成临时副本,
不写生产配置, 不切全局模型。
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib import config  # noqa: E402
from lib import session_model as sm  # noqa: E402

MODELS = [
    {"id": "m-a", "label": "Alpha 27B", "backend_url": "http://a.example/v1", "api_key": "key-a-123456789",
     "backend_type": "openai", "deployment": "vllm", "enable_thinking": True, "thinking_budget": 4096,
     "context_window": 32000, "served_model_name": "alpha-wire", "supports_vision": True},
    {"id": "m-b", "label": "Bravo Flash", "backend_url": "http://b.example/v1", "api_key": "key-b-987654321",
     "backend_type": "openai", "enable_thinking": False, "context_window": 64000},
    {"id": "m-b2", "label": "Bravo Pro", "backend_url": "http://b2.example/v1", "api_key": "key-b2-55555555"},
]
GLOBAL = {"model_id": "glob", "backend_url": "http://global.example/v1", "api_key": "key-global",
          "served_model_name": "glob", "context_window": 200000, "supports_vision": False,
          "enable_thinking": False, "deployment": "", "backend_type": "openai"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """临时 config (models[]) + 临时会话模型存储 + 全局 _LIVE 换成假的全局模型 (测试后自动还原)。"""
    cfg = {"models": [dict(m) for m in MODELS]}
    monkeypatch.setattr(config, "load_cfg", lambda: cfg)
    monkeypatch.setattr(sm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(sm, "STORE", tmp_path / "session_models.json")
    for k, v in GLOBAL.items():
        monkeypatch.setitem(config._LIVE, k, v)
    monkeypatch.setattr(config, "CONTEXT_WINDOW", 200000)
    config._TURN_MODEL.set(None)
    yield cfg
    config._TURN_MODEL.set(None)


class Recorder:
    def __init__(self):
        self.requests = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        sse = 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: [DONE]\n\n'
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})


def _patch_httpx(monkeypatch, module, rec):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(rec.handler)
        return real(*a, **kw)
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


# ── 1. 本轮覆盖只作用于自己的 task, 并发的另一会话仍用全局 ─────────────────

def test_turn_override_isolated_between_concurrent_sessions(env, monkeypatch):
    from lib import transport
    rec = Recorder()
    _patch_httpx(monkeypatch, transport, rec)
    monkeypatch.setattr(transport, "_failover", lambda m, u, k: (m, u, k, False))

    started = asyncio.Event()

    async def session_a():
        tok = config.set_turn_model(config.find_model_entry("m-a"))
        try:
            started.set()
            await asyncio.sleep(0)          # 让 B 在 A 的覆盖生效期间运行
            async for _ in transport.vllm_stream([{"role": "user", "content": "hi"}], []):
                pass
            return config.live_context_window(), config._LIVE["model_id"]
        finally:
            config.reset_turn_model(tok)

    async def session_b():
        await started.wait()
        async for _ in transport.vllm_stream([{"role": "user", "content": "hi"}], []):
            pass
        return config.live_context_window(), config._LIVE["model_id"]

    async def main():
        return await asyncio.gather(session_a(), session_b())

    (cw_a, mid_a), (cw_b, mid_b) = asyncio.run(main())
    assert (cw_a, mid_a) == (32000, "m-a")
    assert (cw_b, mid_b) == (200000, "glob")
    by_host = {r.url.host: r for r in rec.requests}
    ra, rb = by_host["a.example"], by_host["global.example"]
    assert ra.headers["authorization"] == "Bearer key-a-123456789"
    body_a = json.loads(ra.content)
    assert body_a["model"] == "alpha-wire"                       # 线路名 (served_model_name)
    assert body_a["chat_template_kwargs"]["enable_thinking"] is True   # 按 m-a 的思考配置注入
    assert rb.headers["authorization"] == "Bearer key-global"
    assert json.loads(rb.content)["model"] == "glob"
    # 全局值从未被改
    assert dict.get(config._LIVE, "model_id") == "glob"
    assert config._LIVE["model_id"] == "glob"


def test_vision_fallback_still_applies_to_session_model(env, monkeypatch):
    """会话模型不支持视觉 + 消息带图 → 走既有 vision fallback 路由 (按会话模型判断)。"""
    from lib import transport
    monkeypatch.setitem(config._LIVE, "vision_fallback_id", "m-a")
    monkeypatch.setattr(config, "CFG", {"models": MODELS})
    tok = config.set_turn_model(config.find_model_entry("m-b"))
    try:
        msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                                             {"type": "text", "text": "看图"}]}]
        mid, url, key, note = transport._route_to_vision_fallback_if_needed(
            config._LIVE["model_id"], config._LIVE["backend_url"], config._LIVE["api_key"], msgs)
    finally:
        config.reset_turn_model(tok)
    assert (mid, url, key) == ("m-a", "http://a.example/v1", "key-a-123456789") and note


# ── 2. 网关: 会话存了模型 → 本轮用它; 别的会话用默认; 被删 → 回落 + 提示 ─────

class FakeSrv:
    MODEL_ID = "glob"

    def __init__(self):
        self.seen = {}

    def agent_stream(self, user_message, session_id, model, **kw):
        seen = self.seen

        async def gen():
            from lib import thinking_adapter
            from lib.sse import _cur_model
            seen[session_id] = {"label_arg": model, "live": config._LIVE["model_id"],
                                "url": config._LIVE["backend_url"], "cw": config.live_context_window(),
                                "sse_label": _cur_model(),
                                "thinking": thinking_adapter._live_cfg().enabled}
            yield 'data: {"choices":[{"delta":{"msg_id":"x"}}]}\n\n'
            yield "data: [DONE]\n\n"
        return gen()


@pytest.fixture
def gw(env, monkeypatch):
    from gateway import openai_compat as oc
    srv = FakeSrv()
    monkeypatch.setattr(oc, "_srv", lambda: srv)
    monkeypatch.setattr(oc, "require_auth", lambda r: None)
    app = FastAPI()
    app.include_router(oc.router)
    c = TestClient(app)
    c.srv = srv
    return c


def _chat(c, sid):
    r = c.post("/v1/chat/completions", json={"model": "glob", "stream": True, "user": sid,
                                               "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    return r.text


def test_gateway_uses_session_model_only_for_that_session(gw):
    sm.set_model_id("web-s1", "m-a")
    t1 = _chat(gw, "web-s1")
    _chat(gw, "web-s2")
    s1, s2 = gw.srv.seen["web-s1"], gw.srv.seen["web-s2"]
    assert s1 == {"label_arg": "m-a", "live": "m-a", "url": "http://a.example/v1", "cw": 32000,
                  "sse_label": "m-a", "thinking": True}
    assert s2["live"] == "glob" and s2["url"] == "http://global.example/v1" and s2["cw"] == 200000
    assert s2["thinking"] is False
    assert '"session_model"' in t1 and '"per_session": true' in t1
    assert config._TURN_MODEL.get() is None and config._LIVE["model_id"] == "glob"


def test_removed_model_falls_back_to_default_with_note(gw):
    sm.set_model_id("web-s3", "gone-model")
    text = _chat(gw, "web-s3")
    assert gw.srv.seen["web-s3"]["live"] == "glob"
    assert "已不在配置中" in text and '"fallback": true' in text
    entry, note, stored = sm.resolve("web-s3")
    assert entry == {} and stored == "gone-model" and note


# ── 3. /model 指令 ─────────────────────────────────────────────

def test_parse_command():
    assert sm.parse_command("/model") == ""
    assert sm.parse_command("  /model   Bravo Flash ") == "Bravo Flash"
    assert sm.parse_command("/models") is None
    assert sm.parse_command("请用 /model 切换") is None
    assert sm.parse_command("/modelx") is None


def test_handle_command_set_list_ambiguous_clear(env):
    out = sm.handle_command("s", "/model", active_id="glob")
    assert "跟随默认" in out and "m-a" in out and "m-b2" in out
    assert "已切换到 `m-a`" in sm.handle_command("s", "/model alpha")      # label 子串, 不区分大小写
    assert sm.get_model_id("s") == "m-a"
    amb = sm.handle_command("s", "/model bravo")                          # 两个都含 bravo
    assert "多个" in amb and sm.get_model_id("s") == "m-a"
    assert "已切换到 `m-b`" in sm.handle_command("s", "/model m-b")       # 精确 id 优先于子串
    assert "没找到" in sm.handle_command("s", "/model nope")
    assert "跟随默认" in sm.handle_command("s", "/model default")
    assert sm.get_model_id("s") == ""


def test_web_chat_model_command_replies_without_llm(env, monkeypatch):
    from routers import chat_router as cr
    from routers import model_router as mr

    class W:
        HEADERS = {}
        SERVER_URL = "http://never"
        saved = []

        def __init__(self):
            self.s = {"id": "web-cmd1", "messages": [], "owner": "anon"}

        def load_session(self, sid):
            return self.s

        def save_session(self, d):
            self.s = d

        def _save_assistant_msg(self, sid, collected):
            W.saved.append(collected["text"])

    w = W()
    monkeypatch.setattr(cr, "_wui", lambda: w)
    monkeypatch.setattr(cr, "require_auth", lambda r: None)

    async def _active():
        return "glob"
    monkeypatch.setattr(mr, "_active_model_id", _active)

    def _no_llm(*a, **kw):
        raise AssertionError("/model 不该调用上游模型")
    monkeypatch.setattr(cr.httpx, "AsyncClient", _no_llm)
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, "_AUTH_ENABLED", False)
    app = FastAPI()
    app.include_router(cr.router)
    with TestClient(app) as c:
        r = c.post("/api/chat/web-cmd1", json={"message": "/model m-b"})
        assert r.status_code == 200
        assert "已切换到" in r.text and "[DONE]" in r.text
    assert sm.get_model_id("web-cmd1") == "m-b"
    assert w.s["messages"][-1]["content"] == "/model m-b"
    assert W.saved and "m-b" in W.saved[-1]


# ── 4. 会话模型 API ─────────────────────────────────────────────

@pytest.fixture
def api(env, monkeypatch):
    from routers import model_router as mr

    class W:
        def load_session(self, sid):
            return {"id": sid, "messages": [], "owner": "bob" if sid == "web-other" else "anon"}
    monkeypatch.setattr(mr, "_wui", lambda: W())
    monkeypatch.setattr(mr, "require_auth", lambda r: None)

    async def aproxy(path, method="GET", **kw):
        return {"active": "glob", "models": MODELS} if path == "/v1/model/list" else None
    monkeypatch.setattr(mr.S, "aproxy", aproxy)
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, "_AUTH_ENABLED", False)
    app = FastAPI()
    app.include_router(mr.router)
    return TestClient(app)


def test_session_model_api_get_put(api):
    d = api.get("/api/sessions/web-x/model").json()
    assert d["follows_default"] and d["effective"] == "glob" and d["model_id"] is None
    d = api.put("/api/sessions/web-x/model", json={"model_id": "m-b"}).json()
    assert d["ok"] and d["effective"] == "m-b" and d["label"] == "Bravo Flash" and not d["follows_default"]
    assert sm.get_model_id("web-x") == "m-b"
    assert api.put("/api/sessions/web-x/model", json={"model_id": "nope"}).status_code == 404
    d = api.put("/api/sessions/web-x/model", json={"model_id": None}).json()
    assert d["follows_default"] and sm.get_model_id("web-x") == ""
    assert api.get("/api/sessions/web-other/model").status_code == 403    # 别人的会话
    assert api.put("/api/sessions/bad%20id/model", json={}).status_code == 400


# ── 5. 测试模型: 用传入模型自己的 backend/key, 不碰激活模型 ──────────────────

@pytest.fixture
def mgmt(env, monkeypatch):
    from gateway import model_mgmt as mm
    rec = Recorder()
    _patch_httpx(monkeypatch, mm, rec)
    monkeypatch.setattr(mm, "require_auth", lambda r: None)
    app = FastAPI()
    app.include_router(mm.router)
    c = TestClient(app)
    c.rec = rec
    return c


def test_model_test_uses_given_model_backend_and_key(mgmt):
    d = mgmt.post("/v1/model/test", json={"model_id": "m-a"}).json()
    assert d["ok"] and d["model_id"] == "m-a" and d["reply_preview"] == "pong" and "latency_ms" in d
    assert len(mgmt.rec.requests) == 1
    req = mgmt.rec.requests[0]
    assert str(req.url) == "http://a.example/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer key-a-123456789"
    body = json.loads(req.content)
    assert body["model"] == "alpha-wire" and body["max_tokens"] <= 64 and body["stream"] is False
    assert body["chat_template_kwargs"]["enable_thinking"] is False      # 测试时关思考
    assert config._LIVE["model_id"] == "glob" and config._TURN_MODEL.get() is None


def test_model_test_ignores_caller_url_for_configured_model(mgmt):
    """已配置的 id + 调用方自带 backend_url: 只用配置里的地址和 key (防把服务端 key 发去任意主机)。"""
    d = mgmt.post("/v1/model/test", json={"model_id": "m-b", "backend_url": "http://evil.example/v1"}).json()
    assert d["ok"]
    hosts = [r.url.host for r in mgmt.rec.requests]
    assert hosts == ["b.example"]


def test_model_test_raw_url_never_gets_server_key(mgmt):
    d = mgmt.post("/v1/model/test", json={"model_id": "new-one", "backend_url": "http://new.example/v1"}).json()
    assert d["ok"]
    req = mgmt.rec.requests[0]
    assert req.url.host == "new.example"
    auth = req.headers.get("authorization", "")
    assert auth == ""                    # 不带任何服务端 key
    # 回传脱敏值也不会被当成 key
    mgmt.post("/v1/model/test", json={"id": "new-one", "backend_url": "http://new.example/v1", "api_key": "••••6789"})
    assert "••••" not in mgmt.rec.requests[-1].headers.get("authorization", "")


def test_model_test_unknown_model(mgmt):
    d = mgmt.post("/v1/model/test", json={"model_id": "nope"}).json()
    assert d["ok"] is False and "不在配置" in d["error"] and not mgmt.rec.requests


def test_model_list_masks_api_keys(mgmt, monkeypatch):
    from gateway import model_mgmt as mm

    class Srv:
        MODEL_ID, BACKEND_URL, BACKEND_TYPE = "glob", "http://global.example/v1", "openai"
    monkeypatch.setattr(mm, "_srv", lambda: Srv)
    monkeypatch.setattr(mm, "load_cfg", lambda: {"models": [dict(m, extra={"token": "tok-secret-xyz"}) for m in MODELS]})
    d = mgmt.get("/v1/model/list").json()
    raw = json.dumps(d, ensure_ascii=False)
    for m in MODELS:
        assert m["api_key"] not in raw
    assert "tok-secret-xyz" not in raw
    assert d["models"][0]["api_key"] == "••••6789" and d["models"][0]["extra"]["token"].startswith("••••")
    assert d["models"][0]["backend_url"] == "http://a.example/v1"     # 非敏感字段原样
