"""能力接入 (lib/providers.py / provider_probe.py / provider_gen.py / routers/providers_router.py) 单测。
配置读写走 LITECODE_CONFIG 指向的临时副本; 外部 HTTP 全部 httpx.MockTransport, 不联网。"""
import asyncio
import base64
import json

import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
import lib.config  # noqa: E402,F401  先用仓库默认配置初始化模块级常量, 之后 LITECODE_CONFIG 只影响 load_cfg

from lib import provider_gen, provider_probe
from lib import providers as P

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
_REAL_CLIENT = httpx.AsyncClient
KEY = "sk-EXAMPLEabcdefghijklmnop1234"   # 假密钥一律 sk-EXAMPLE 开头 (公开包自检放行)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"server": {"port": 1, "token": "t"}, "model": {"id": "m", "backend_url": "http://x"},
                             "models": [{"id": "m", "api_key": "k"}], "search": {"searxng_url": "http://s"}}))
    monkeypatch.setenv("LITECODE_CONFIG", str(p))
    return p


def _mock(monkeypatch, handler):
    """所有 httpx.AsyncClient 走 MockTransport; 返回请求记录。"""
    seen = []
    real = _REAL_CLIENT

    def h(req):
        seen.append(req)
        return handler(req)

    def factory(*a, **kw):
        kw.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(h), **kw)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def run(coro):
    return asyncio.run(coro)


# ── 配置读写 ──

def test_crud_keeps_other_fields_and_defaults(cfg):
    a = P.create({"name": "图A", "kind": "image", "protocol": "openai_images", "base_url": "http://a/v1", "api_key": KEY})
    b = P.create({"name": "图B", "kind": "image", "protocol": "comfyui", "base_url": "127.0.0.1:8188"})
    raw = json.loads(cfg.read_text())
    assert raw["models"] == [{"id": "m", "api_key": "k"}] and raw["search"] == {"searxng_url": "http://s"}
    assert [p["name"] for p in raw["providers"]] == ["图A", "图B"]
    assert a["default"] and not b["default"]           # 同类第一张自动默认
    P.set_default(b["id"])
    got = {p["name"]: p["default"] for p in P.load_all()}
    assert got == {"图A": False, "图B": True}
    # 打码占位回传 = 不改 key
    P.update(a["id"], {"api_key": P.masked(P.find(a["id"]))["api_key"], "note": "新备注"})
    assert P.find(a["id"])["api_key"] == KEY and P.find(a["id"])["note"] == "新备注"
    with pytest.raises(ValueError):
        P.create({"name": "图a", "kind": "image"})       # 重名 (不分大小写)
    with pytest.raises(ValueError):
        P.create({"name": "", "kind": "image"})
    assert P.delete(b["id"]) and P.find("图A")["default"]   # 删默认 → 同类接任
    assert not P.delete("nope")


def test_normalize_unknown_kind_protocol():
    p = P.normalize({"name": "x", "kind": "weird", "protocol": "zzz", "custom": 1})
    assert p["kind"] == "other" and p["protocol"] == "http" and p["custom"] == 1 and p["id"].startswith("p_")


def test_masked_hides_key_everywhere():
    p = P.normalize({"name": "x", "api_key": KEY, "usage": f"curl -H 'Authorization: Bearer {KEY}' http://a",
                     "extra": {"secret": "SECabcdefghijklmnop", "region": "cn"}})
    m = P.masked(p)
    assert KEY not in json.dumps(m, ensure_ascii=False)
    assert m["api_key"].startswith("••••") and m["api_key"].endswith(KEY[-4:])
    assert m["extra"]["secret"].startswith("••••") and m["extra"]["region"] == "cn"
    assert m["has_key"] is True
    assert P.masked(P.normalize({"name": "y", "api_key": "short"}))["api_key"] == "••••"


def test_render_and_card_text():
    p = P.normalize({"name": "生图", "kind": "image", "base_url": "http://a/v1/", "api_key": KEY, "model": "m1",
                     "extra": {"region": "cn"},
                     "usage": "curl {base_url}/x -H 'Authorization: Bearer {api_key}' -d '{\"model\": \"{model}\", \"p\": \"{prompt}\"}' {unknown} {region}"})
    s = P.render(p["usage"], p, prompt="猫")
    assert "http://a/v1/x" in s and KEY in s and '{"model": "m1", "p": "猫"}' in s and "{unknown}" in s and " cn" in s
    card = P.card_text(p)
    assert KEY in card and "生图" in card and "http://a/v1/" in card


# ── curl 解析 ──

def test_first_curl_and_parse():
    usage = """# 说明
```bash
curl -s -X POST https://api.x.com/v1/search \\
  -H 'Authorization: Bearer abc' -H 'Content-Type: application/json' \\
  -d '{"query": "hi"}' | python3 -m json.tool
```
curl second"""
    cmd = P.first_curl(usage)
    r = P.parse_curl(cmd)
    assert r["method"] == "POST" and r["url"] == "https://api.x.com/v1/search"
    assert r["headers"]["Authorization"] == "Bearer abc" and r["content"] == b'{"query": "hi"}'
    g = P.parse_curl("curl -G 127.0.0.1:8888/search -d q=a -d format=json")
    assert g["method"] == "GET" and g["url"] == "http://127.0.0.1:8888/search?q=a&format=json"
    f = P.parse_curl("curl -sS https://a/b -F model=whisper -F lang=zh -u u:p")
    assert f["files"] == {"model": (None, "whisper"), "lang": (None, "zh")} and f["auth"] == ("u", "p")
    assert P.parse_curl("curl https://a -o out.mp3 -m 5")["timeout"] == 5.0
    for bad in ("curl $B/api", "curl https://a -d @body.json", "curl https://a -F file=@a.mp3",
                "curl --weird https://a", "wget https://a", "curl -s"):
        with pytest.raises(ValueError):
            P.parse_curl(bad)
    assert P.first_curl("没有命令") is None


# ── 路由 ──

def test_candidates_order_and_builtin():
    items = [P.normalize(x) for x in (
        {"id": "a", "name": "A", "kind": "image"}, {"id": "b", "name": "B", "kind": "image", "default": True},
        {"id": "c", "name": "C", "kind": "image", "enabled": False}, {"id": "d", "name": "D", "kind": "video"})]
    assert [p["id"] for p in P.candidates("image", items=items)] == ["b", "a"]
    assert [p["id"] for p in P.candidates("image", "a", items)] == ["a", "b"]
    assert [p["id"] for p in P.candidates("image", "C", items)] == ["c", "b", "a"]   # 指定的停用卡也先试
    with pytest.raises(LookupError):
        P.candidates("image", "nope", items)
    assert [p["id"] for p in P.candidates("music", items=items)] == ["builtin_bgm"]


def test_generate_image_falls_back_and_saves(tmp_path, monkeypatch):
    items = [P.normalize(x) for x in (
        {"name": "坏的", "kind": "image", "protocol": "openai_images", "base_url": "http://bad/v1", "default": True},
        {"name": "b64", "kind": "image", "protocol": "openai_images", "base_url": "http://ok/v1", "model": "gm",
         "api_key": KEY},
        {"name": "sf", "kind": "image", "protocol": "openai_images", "base_url": "http://sf/v1",
         "extra": {"size_field": "image_size"}})]

    def h(req):
        if req.url.host == "bad":
            return httpx.Response(500, text="boom")
        if req.url.host == "ok":
            body = json.loads(req.content)
            assert body == {"prompt": "猫", "n": 1, "model": "gm", "size": "512x256"}
            assert req.headers["authorization"] == f"Bearer {KEY}"
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        if req.url.path.endswith("/images/generations"):
            assert json.loads(req.content)["image_size"] == "512x256"
            return httpx.Response(200, json={"images": [{"url": "http://cdn/x.png"}]})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    _mock(monkeypatch, h)
    r = run(provider_gen.generate_image("猫", 512, 256, out_dir=tmp_path, items=items))
    assert r["ok"] and r["provider"] == "b64" and r["tried"][0][0] == "坏的"
    assert open(r["files"][0], "rb").read() == PNG and r["files"][0].endswith(".png")
    r2 = run(provider_gen.generate_image("猫", 512, 256, provider="sf", out_dir=tmp_path, items=items))
    assert r2["ok"] and r2["provider"] == "sf"


def test_generate_image_http_returns_manual(tmp_path):
    items = [P.normalize({"name": "万相", "kind": "image", "protocol": "http", "api_key": KEY,
                          "base_url": "https://dash", "usage": "curl {base_url}/x -H 'Authorization: Bearer {api_key}'"})]
    r = run(provider_gen.generate_image("猫", out_dir=tmp_path, items=items))
    assert not r["ok"] and r["provider"] == "万相" and KEY in r["manual"] and "https://dash/x" in r["manual"]
    none = run(provider_gen.generate_image("猫", out_dir=tmp_path, items=[]))
    assert not none["ok"] and none["tried"] == []


def test_comfy_output_parse_and_video_spawn(tmp_path, monkeypatch):
    files, res = provider_gen.parse_comfy_output("[run] x\n[out] /tmp/a.mp4  (12 KB)  http://v\nRESULT {\"wall_s\": 3}\n")
    assert files == ["/tmp/a.mp4"] and res == {"wall_s": 3}
    calls = []
    monkeypatch.setattr(provider_gen, "_spawn", lambda args, log, sid: calls.append(args) or "4242")
    items = [P.normalize({"name": "H3", "kind": "video", "protocol": "comfyui", "base_url": "10.0.0.1:8188",
                          "extra": {"via_ssh": "gpu-a"}})]
    r = run(provider_gen.generate_video("猫跑", frames=73, image="/tmp/f.png", out_dir=tmp_path, items=items))
    assert r["ok"] and r["async"] and r["pid"] == "4242"
    a = calls[0]
    assert a[2:4] == ["--via-ssh", "gpu-a"] and "h3_i2v" in a and "first=/tmp/f.png" in a and "73" in a


# ── 连通测试 ──

def test_probe_protocols(monkeypatch):
    def h(req):
        u = str(req.url)
        if "/search" in u:
            return httpx.Response(200, json={"results": [{"title": "a"}, {"title": "b"}]})
        if u.endswith("/system_stats"):
            return httpx.Response(200, json={"system": {"comfyui_version": "0.39"}, "devices": [{"name": "cuda:0 RTX"}]})
        if u.endswith("/models"):
            return httpx.Response(401 if "bad" in u else 200, json={"data": [{"id": "m1"}]})
        if u.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1] * 8}]})
        if u.endswith("/rerank"):
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}]})
        if "curltest" in u:
            assert req.headers["x-k"] == KEY and req.method == "POST" and b"LiteCode" in req.content
            return httpx.Response(200, json={"ok": 1})
        return httpx.Response(404)
    _mock(monkeypatch, h)
    t = lambda **kw: run(provider_probe.test(P.normalize(kw)))
    r = t(name="s", protocol="searxng", base_url="127.0.0.1:18888")
    assert r["ok"] and "2 条" in r["detail"] and r["ms"] >= 0
    assert "0.39" in t(name="c", protocol="comfyui", base_url="http://c")["detail"]
    assert t(name="o", protocol="openai_images", base_url="http://o/v1", model="m1")["ok"]
    bad = t(name="o", protocol="openai_tts", base_url="http://bad/v1")
    assert not bad["ok"] and "鉴权" in bad["error"]
    assert "维度 8" in t(name="e", protocol="openai_embeddings", base_url="http://e/v1")["detail"]
    assert t(name="r", protocol="openai_rerank", base_url="http://r/v1")["ok"]
    r = t(name="h", protocol="http", api_key=KEY,
          usage="curl -X POST http://curltest/x -H 'X-K: {api_key}' -d '{\"q\": \"{query}\"}'")
    assert r["ok"], r
    r = t(name="h2", protocol="http", usage="curl $B/x")
    assert not r["ok"] and "shell 变量" in r["error"]


def test_webhooks_and_signing(monkeypatch):
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, json={"errcode": 0, "code": 0}))
    wecom = P.normalize({"name": "w", "kind": "notify", "protocol": "wecom_webhook", "api_key": "KK"})
    assert run(provider_probe.test(wecom))["ok"]
    assert str(seen[-1].url) == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=KK"
    assert json.loads(seen[-1].content) == {"msgtype": "text", "text": {"content": provider_probe.TEST_TEXT}}
    fs = P.normalize({"name": "f", "kind": "notify", "protocol": "feishu_webhook", "base_url": "https://open.feishu.cn/hook/x",
                      "extra": {"secret": "sec"}})
    assert run(provider_probe.send_notify(fs, "hi"))["ok"]
    body = json.loads(seen[-1].content)
    assert body["msg_type"] == "text" and body["sign"] == provider_probe._sign_feishu("sec", int(body["timestamp"]))
    dd = P.normalize({"name": "d", "kind": "notify", "protocol": "dingtalk_webhook",
                      "base_url": "https://oapi.dingtalk.com/robot/send?access_token=t", "extra": {"secret": "SEC1"}})
    assert run(provider_probe.send_notify(dd, "hi"))["ok"]
    q = seen[-1].url.params
    assert q["access_token"] == "t" and q["timestamp"] and q["sign"]
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook"}))
    r = run(provider_probe.send_notify(wecom, "hi"))
    assert not r["ok"] and "93000" in r["error"]


def test_dingtalk_sign_matches_reference():
    import hashlib, hmac, urllib.parse
    ts, secret = 1700000000000, "SECxyz"
    ref = urllib.parse.quote_plus(base64.b64encode(
        hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()))
    assert provider_probe._sign_dingtalk(secret, ts) == ref


def test_s3_sigv4_shape(monkeypatch):
    import datetime as dt
    url, h = provider_probe.s3_list_request("http://127.0.0.1:9000", "AK", "SK", "us-east-1",
                                            now=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc))
    assert url == "http://127.0.0.1:9000/" and h["x-amz-date"] == "20260102T030405Z"
    assert h["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/20260102/us-east-1/s3/aws4_request")
    _mock(monkeypatch, lambda req: httpx.Response(200, text="<R><Buckets><Bucket><Name>b1</Name></Bucket></Buckets></R>"))
    r = run(provider_probe.test(P.normalize({"name": "m", "protocol": "s3", "base_url": "http://127.0.0.1:9000",
                                             "api_key": "SK", "extra": {"access_key_id": "AK"}})))
    assert r["ok"] and "b1" in r["detail"]


def test_turn_refs_block(cfg):
    p = P.create({"name": "博查", "kind": "search", "protocol": "http", "api_key": KEY, "usage": "curl {base_url}"})
    P.turn_begin([p["id"], p["id"], "gone"])
    assert P.turn_refs() == (p["id"], "gone")
    block = P.refs_block(P.turn_refs())
    assert block.startswith(P.REF_LABEL) and KEY in block and "已被删除" in block
    assert P.ref_meta([p["id"], "gone", 3]) == [{"id": p["id"], "name": "博查", "kind": "search"}]


# ── 接口 ──

@pytest.fixture
def client(cfg, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import providers_router as R
    monkeypatch.setattr(R, "require_auth", lambda request: None)
    app = FastAPI()
    app.include_router(R.router)
    return TestClient(app)


def test_router_crud_mask_test(client, monkeypatch):
    r = client.post("/api/providers", json={"name": "SearXNG", "kind": "search", "protocol": "searxng",
                                            "base_url": "http://127.0.0.1:18888", "api_key": KEY})
    assert r.status_code == 200
    pid = r.json()["provider"]["id"]
    lst = client.get("/api/providers").json()
    assert KEY not in json.dumps(lst) and lst["providers"][0]["default"] is True
    assert lst["builtins"][0]["readonly"] and "image" in lst["kinds"]
    full = client.get(f"/api/providers/{pid}").json()
    assert full["provider"]["api_key"] == KEY and KEY in full["card"]
    assert client.put(f"/api/providers/{pid}", json={"note": "本机", "api_key": "••••1234"}).status_code == 200
    assert client.get(f"/api/providers/{pid}").json()["provider"]["api_key"] == KEY
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"results": [{}]}))
    t = client.post(f"/api/providers/{pid}/test").json()
    assert t["ok"] and "1 条" in t["detail"]
    d = client.post("/api/providers/test", json={"id": pid, "base_url": "http://other:1", "api_key": "••••"}).json()
    assert d["ok"]
    assert client.put("/api/providers/builtin_bgm", json={"name": "x"}).status_code == 400
    assert client.post("/api/providers/builtin_bgm/test").json()["ok"]
    assert client.get("/api/providers/presets").json()["presets"]
    assert client.post("/api/providers", json={"name": "SearXNG"}).status_code == 400
    assert client.post("/api/providers", content=b"[]").status_code == 400
    assert client.delete(f"/api/providers/{pid}").json()["ok"]
    assert client.delete(f"/api/providers/{pid}").status_code == 404
    assert client.get(f"/api/providers/{pid}").status_code == 404


def test_presets_well_formed():
    from lib.provider_presets import PRESETS
    keys = [p["key"] for p in PRESETS]
    assert len(keys) == len(set(keys))
    for p in PRESETS:
        assert p["kind"] in P.KINDS and p["protocol"] in P.PROTOCOLS, p["key"]
        if p["protocol"] == "http" and p["verified"]:
            assert P.first_curl(p["usage"]), p["key"]
    must = {"openai_images", "comfyui_qwen", "siliconflow_image", "ark_seedream", "ark_seedance", "dashscope_wan_image",
            "dashscope_cosyvoice", "minimax_hailuo", "minimax_tts", "kling", "searxng", "bocha", "tavily", "custom_http",
            "minimax_music", "suno", "wecom_bot", "feishu_bot", "dingtalk_bot", "smtp", "minio"}
    assert must <= set(keys)


def test_tool_handlers(cfg, monkeypatch, tmp_path):
    from core.tools.handlers import providers as H
    monkeypatch.setattr(H, "_artifact_dir", lambda sid: tmp_path)
    out, _ = run(H.h_list_providers("s", {}))
    assert "本地 BGM" in out
    P.create({"name": "群", "kind": "notify", "protocol": "wecom_webhook", "api_key": "KK"})
    assert "群" in run(H.h_list_providers("s", {"kind": "notify"}))[0]
    assert "KK" in run(H.h_get_provider("s", {"name": "群"}))[0]
    assert run(H.h_get_provider("s", {"name": "无"}))[0].startswith("ERROR")
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"errcode": 0}))
    assert "OK" in run(H.h_send_notify("s", {"text": "hi"}))[0]
    out, _ = run(H.h_generate_image("s", {"prompt": "猫"}))
    assert out.startswith("ERROR") and "能力接入" in out


def test_reimport_keeps_provider_chips():
    """web 缓存重导 (server 历史更新) 时, 用户消息上的 providers chip 要带过来。"""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "web_ui.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    want = ("_TURN_META", "_turns", "_merge_cached_turn_meta")
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in want)
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in want for t in n.targets))]
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(src), "exec"), ns)
    refs = [{"id": "p_1", "name": "博查", "kind": "search"}]
    old = [{"role": "user", "content": "查一下", "providers": refs}, {"role": "assistant", "content": "好"}]
    new = [{"role": "user", "content": "查一下"}, {"role": "assistant", "content": "好", "trace_steps": [1]}]
    ns["_merge_cached_turn_meta"](old, new)
    assert new[0]["providers"] == refs
    other = [{"role": "user", "content": "别的问题"}]
    ns["_merge_cached_turn_meta"](old, other)
    assert "providers" not in other[0]
