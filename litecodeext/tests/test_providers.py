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


# ── 2026-10-23 小白化: 明文自动识别 / 自动发现 / 测试失败人话原因 ──

def test_infer_from_plain_text():
    from lib.provider_infer import infer
    d = infer({"name": "运营群", "usage": "钉钉运营群机器人 webhook https://oapi.dingtalk.com/robot/send?access_token=tk123456 "
                                        "加签密钥 SEC0123456789abcdef0123, 日报发这里"})
    assert d == {"base_url": "https://oapi.dingtalk.com/robot/send?access_token=tk123456", "kind": "notify",
                 "protocol": "dingtalk_webhook", "extra.secret": "SEC0123456789abcdef0123"}
    tpl = infer({"usage": "开了加签 LiteCode 会自动计算\ncurl 'https://oapi.dingtalk.com/robot/send?access_token=粘贴你的token'"})
    assert "base_url" not in tpl and "extra.secret" not in tpl          # 模板占位不当真值
    fs = infer({"usage": "签名校验密钥: abcSecret99\ncurl -X POST 'https://open.feishu.cn/open-apis/bot/v2/hook/h00k'"})
    assert fs["protocol"] == "feishu_webhook" and fs["extra.secret"] == "abcSecret99"
    sf = infer({"usage": f"Key: {KEY}\ncurl -s https://api.siliconflow.cn/v1/images/generations -H 'Authorization: Bearer {{api_key}}' "
                         "-d '{\"model\": \"Qwen/Qwen-Image\", \"prompt\": \"{prompt}\"}'"})
    assert sf == {"model": "Qwen/Qwen-Image", "api_key": KEY, "protocol": "openai_images", "kind": "image",
                  "base_url": "https://api.siliconflow.cn/v1"}
    mail = infer({"usage": "SMTP 服务器: smtp.qq.com:465\n发件邮箱: me@qq.com\n授权码: abcdabcdabcd\n发给: a@b.com, c@d.com"})
    assert mail["protocol"] == "smtp" and mail["api_key"] == "abcdabcdabcd" and mail["extra.to"] == "a@b.com, c@d.com"
    cf = infer({"name": "ComfyUI", "usage": "地址: 10.0.0.5:8188\n做视频用"})
    assert cf == {"base_url": "10.0.0.5:8188", "protocol": "comfyui", "kind": "video"}
    assert infer({"usage": "随便写的一段话, 啥也识别不出"}) == {}


def test_presets_have_how_and_curl():
    from lib.provider_infer import curls
    from lib.provider_presets import PRESETS
    for p in PRESETS:
        assert p["how"] and p["usage"], p["key"]
        if p["verified"] and p["protocol"] not in ("smtp", "s3"):
            assert curls(p["usage"]), p["key"]
        assert "{api_key}" not in p["how"]
    need = {"dingtalk_bot", "feishu_bot", "wecom_bot", "smtp", "siliconflow_image", "ark_seedream", "dashscope_wan_image",
            "minimax_hailuo", "bocha", "tavily", "comfyui_qwen", "openai_images", "sensevoice_gpu", "acestep_gpu",
            "indextts2_gpu", "siliconflow_asr"}
    assert need <= {p["key"] for p in PRESETS}


def test_create_infers_and_reinfers_on_edit(cfg):
    p = P.create({"name": "群", "usage": "钉钉 webhook https://oapi.dingtalk.com/robot/send?access_token=aaa111"})
    assert p["kind"] == "notify" and p["protocol"] == "dingtalk_webhook" and p["base_url"].endswith("aaa111")
    assert set(p["inferred"]) >= {"kind", "protocol", "base_url"}
    q = P.update(p["id"], {"usage": "钉钉 webhook https://oapi.dingtalk.com/robot/send?access_token=bbb222 加签密钥 SEC00112233445566778899"})
    assert q["base_url"].endswith("bbb222") and q["extra"]["secret"] == "SEC00112233445566778899"
    r = P.update(p["id"], {"base_url": "https://x/robot/send?access_token=manual",
                           "usage": "钉钉 https://oapi.dingtalk.com/robot/send?access_token=ccc"})
    assert r["base_url"].endswith("manual")                      # 手填的高级字段优先, 不被识别覆盖
    plain = P.create({"name": "说明卡", "usage": "公司内网有个服务, 找张三要地址"})
    assert plain["kind"] == "other" and plain["protocol"] == "http"   # 识别不出也照样保存
    old = P.create({"name": "旧卡", "kind": "image", "protocol": "comfyui", "base_url": "1.2.3.4:8188"})
    assert P.update(old["id"], {"note": "x"})["protocol"] == "comfyui"


def test_discover_existing(tmp_path, monkeypatch):
    from lib import provider_discover as PD
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "comfyui_endpoints.json").write_text(json.dumps({
        "http://10.0.0.5:8188": {"url": "http://10.0.0.5:8188", "via": "gpu-a", "last_used": "2026-10-22"}}))
    monkeypatch.setenv("WORKSPACE", str(ws))
    monkeypatch.delenv("COMFY_ENDPOINTS_FILE", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    got = PD.discover({"search": {"searxng_url": "http://sx:8080/"}}, items=[])
    by = {x["id"]: x for x in got}
    assert by["auto_searxng"]["base_url"] == "http://sx:8080" and by["auto_searxng"]["readonly"]
    c = by["auto_comfy_10_0_0_5_8188"]
    assert c["protocol"] == "comfyui" and c["base_url"] == "10.0.0.5:8188" and c["extra"] == {"via_ssh": "gpu-a"}
    assert "auto_tts" in by and all(x["auto"] for x in got)
    mine = [P.normalize({"name": "m", "protocol": "comfyui", "base_url": "http://10.0.0.5:8188"}),
            P.normalize({"name": "s", "protocol": "searxng", "base_url": "http://sx:8080"})]
    ids = {x["id"] for x in PD.discover({"search": {"searxng_url": "http://sx:8080"}}, items=mine)}
    assert "auto_comfy_10_0_0_5_8188" not in ids and "auto_searxng" not in ids   # 用户已手动加过就不重复
    monkeypatch.setattr(PD, "_tcp_ok", lambda h, p, timeout=0.3: False)
    assert "auto_searxng" not in {x["id"] for x in PD.discover({}, items=[])}


def test_failure_hints(monkeypatch):
    from lib.provider_hints import explain

    def dd(errcode, errmsg, secret=""):
        _mock(monkeypatch, lambda req: httpx.Response(200, json={"errcode": errcode, "errmsg": errmsg}))
        p = P.normalize({"name": "d", "kind": "notify", "protocol": "dingtalk_webhook",
                         "base_url": "https://oapi.dingtalk.com/robot/send?access_token=t", "extra": {"secret": secret}})
        return run(provider_probe.test(p))
    r = dd(310000, "sign not match, more: [https://ding-doc.dingtalk.com]", "SEC1")
    assert not r["ok"] and r["hint"].startswith("加签不对")
    assert "没写密钥" in dd(310000, "sign not match")["hint"]
    assert "关键词" in dd(310000, "keywords not in content")["hint"]
    assert "白名单" in dd(310000, "ip 1.2.3.4 not in whitelist")["hint"]
    assert dd(0, "ok")["ok"]
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"code": 19021, "msg": "sign match fail or timestamp is not within one hour"}))
    fs = run(provider_probe.test(P.normalize({"name": "f", "kind": "notify", "protocol": "feishu_webhook",
                                              "base_url": "https://open.feishu.cn/open-apis/bot/v2/hook/x"})))
    assert "签" in fs["hint"]
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook url"}))
    wc = run(provider_probe.test(P.normalize({"name": "w", "kind": "notify", "protocol": "wecom_webhook", "api_key": "K"})))
    assert "Webhook 地址无效" in wc["hint"]
    _mock(monkeypatch, lambda req: httpx.Response(401, json={"error": "invalid key"}))
    k = run(provider_probe.test(P.normalize({"name": "o", "protocol": "openai_images", "base_url": "http://o/v1"})))
    assert k["hint"].startswith("Key 不对")

    def boom(req):
        raise httpx.ConnectError("refused")
    _mock(monkeypatch, boom)
    c = run(provider_probe.test(P.normalize({"name": "c", "protocol": "comfyui", "base_url": "10.9.9.9:8188"})))
    assert c["hint"].startswith("地址打不开")
    tpl = P.normalize({"name": "t", "kind": "notify", "protocol": "dingtalk_webhook",
                       "usage": "curl 'https://oapi.dingtalk.com/robot/send?access_token=粘贴你的token'"})
    assert "粘贴" in run(provider_probe.test(tpl))["hint"]
    assert "授权码" in explain({"protocol": "smtp"}, {"ok": False, "error": "SMTPAuthenticationError: (535, b'Login fail')"})
    for st, word in ((404, "路径"), (429, "频繁"), (502, "出错"), (402, "余额")):
        assert word in explain({"protocol": "http"}, {"ok": False, "status": st, "error": "x"})


def test_probe_curl_and_untested(monkeypatch):
    seen = _mock(monkeypatch, lambda req: httpx.Response(404 if "litecode-test" in str(req.url) else 200,
                                                         json={"code": "NotFound"}))
    usage = (f"Key: {KEY}\ncurl -s -X POST https://dash/api/v1/services/x -H 'Authorization: Bearer {{api_key}}' -d '{{}}'\n"
             "# 测试用 (只验证 Key)\ncurl -s https://dash/api/v1/tasks/litecode-test -H 'Authorization: Bearer {api_key}'")
    p = P.prepare_draft({"name": "万相", "usage": usage})
    r = run(provider_probe.test(p))
    assert r["ok"] and "Key 有效" in r["detail"] and seen[-1].method == "GET"
    assert seen[-1].headers["authorization"] == f"Bearer {KEY}"
    _mock(monkeypatch, lambda req: httpx.Response(401, json={"code": "InvalidApiKey"}))
    assert run(provider_probe.test(p))["hint"].startswith("Key 不对")
    u = run(provider_probe.test(P.prepare_draft({"name": "说明", "usage": "公司内网服务, 找张三要地址"})))
    assert u["ok"] and u["untested"] and "自行调用" in u["detail"]


def test_router_adopt_and_record(client, monkeypatch):
    lst = client.get("/api/providers").json()
    ids = [b["id"] for b in lst["builtins"]]
    assert "builtin_bgm" in ids and "auto_searxng" in ids                    # cfg 里 search.searxng_url = http://s
    assert client.put("/api/providers/auto_searxng", json={"name": "x"}).status_code == 400
    a = client.post("/api/providers/auto_searxng/adopt").json()["provider"]
    assert a["protocol"] == "searxng" and a["base_url"] == "http://s" and not a.get("readonly")
    assert "auto_searxng" not in [b["id"] for b in client.get("/api/providers").json()["builtins"]]
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"results": [{}]}))
    assert client.post(f"/api/providers/{a['id']}/test").json()["ok"]
    assert P.find(a["id"])["last_test"]["ok"] is True
    d = client.post("/api/providers/test", json={"name": "群", "usage": "钉钉 http://fake/robot/send?access_token=ok"}).json()
    assert d["recognized"] == {"kind": "notify", "protocol": "dingtalk_webhook"}


def test_send_notify_manual_fallback(cfg):
    from core.tools.handlers import providers as H
    P.create({"name": "老板微信", "kind": "notify", "usage": "发到老板个人微信: 用 wechat 工具发给 wxid_boss"})
    out, _ = run(H.h_send_notify("s", {"text": "日报好了", "channel": "老板微信"}))
    assert "不是能自动发送的渠道" in out and "wxid_boss" in out and "日报好了" in out


def test_curl_multiline_quoted_body():
    """2026-10-23 实跑发现: -d '{ … }' 引号里跨多行 (多行 JSON / 歌词) 时只截到第一行, 测试报"引号不闭合"。"""
    from lib.provider_infer import curls
    text = ("提交任务:\n```bash\ncurl -s -X POST http://h:1/release_task \\\n  -H 'Content-Type: application/json' \\\n"
            "  -d '{\n    \"prompt\": \"edm\",\n    \"lyrics\": \"[verse]\\n第一行\\n\"\n  }'\n# 下一条\n```\n"
            "curl -s http://h:1/health\n")
    cs = curls(text)
    assert len(cs) == 2 and cs[1] == "curl -s http://h:1/health"
    req = P.parse_curl(cs[0])
    assert req["method"] == "POST" and req["url"] == "http://h:1/release_task"
    body = req.get("content") or json.dumps(req.get("json"))
    assert json.loads(body)["lyrics"] == "[verse]\n第一行\n"
    assert P.first_curl(text) == cs[0]
