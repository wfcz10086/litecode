"""manage_models: 预设补全 / key 脱敏 / 连通失败不写入 / 不准删默认 / 切默认同步 default_model_id。
全部在临时 config (LITECODE_CONFIG) 上跑, 上游接口用 httpx.MockTransport 假装。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import httpx  # noqa: E402
import pytest  # noqa: E402

import lib.config as config  # noqa: E402
from core import model_admin as ma  # noqa: E402
from gateway import model_mgmt as mm  # noqa: E402

KEY = "sk-abcdefgh-ijklmnopqrstuvwx1234"
DEFAULT = {"id": "base-model", "label": "Base", "backend_type": "openai", "backend_url": "http://base.example/v1",
           "api_key": "sk-basebase-basebase9999", "api_format": "openai-chat", "context_window": 32000}


class Upstream:
    """假上游: chat/completions 按 behavior 返回; /models 列出 ids。记录所有请求。"""
    def __init__(self, behavior="ok", models=("deepseek-flash",), vision=True, thinking=True, models_ok_hosts=None):
        self.behavior, self.models, self.vision, self.thinking = behavior, list(models), vision, thinking
        self.models_ok_hosts = models_ok_hosts
        self.requests = []

    def handler(self, req: httpx.Request):
        self.requests.append(req)
        if req.url.path.endswith("/models"):
            if self.models_ok_hosts is not None and req.url.host not in self.models_ok_hosts:
                return httpx.Response(401, json={"error": "invalid key"})
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        if self.behavior == "401":
            return httpx.Response(401, json={"error": {"message": f"Authentication Fails, Your api key: {KEY} is invalid"}})
        if self.behavior == "404":
            return httpx.Response(404, json={"error": {"message": "Model Not Exist"}})
        body = json.loads(req.content)
        content = body["messages"][0]["content"]
        if isinstance(content, list):   # 看图探测
            if not self.vision:
                return httpx.Response(400, json={"error": "image input not supported"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "红色"}}]})
        if "17*23" in content:           # 思考探测
            msg = {"content": "391"}
            if self.thinking:
                msg["reasoning_content"] = "17*23=391"
            return httpx.Response(200, json={"choices": [{"message": msg}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    cfg = {"server": {"port": 1}, "model": dict(DEFAULT), "default_model_id": "base-model",
           "models": [dict(DEFAULT), dict(DEFAULT, id="spare", backend_url="http://spare.example/v1")]}
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))
    monkeypatch.setenv("LITECODE_CONFIG", str(p))

    class Srv:
        MODEL_ID = "base-model"
    monkeypatch.setattr(mm, "_srv", lambda: Srv)
    up = Upstream()
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(up.handler)
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    config._TURN_MODEL.set(None)
    yield p, up
    config._TURN_MODEL.set(None)


def _models(p):
    return {m["id"]: m for m in json.loads(p.read_text())["models"]}


def run(args):
    return asyncio.run(ma.run(args))


def test_config_path_env_override(env):
    p, _ = env
    assert config.config_path() == p and config.load_cfg()["default_model_id"] == "base-model"


def test_presets_resolution():
    assert ma.preset_by_name("deepseek") == "deepseek"
    assert ma.preset_by_name("智谱") == "glm" and ma.preset_by_name("阿里百炼") == "dashscope"
    assert ma.preset_by_name("火山方舟") == "ark" and ma.preset_by_name("硅基流动") == "siliconflow"
    assert ma.preset_by_url("https://api.moonshot.cn/v1") == "kimi"
    assert ma.guess_by_key("sk-or-v1-xxxx") == ("openrouter",)
    assert ma.guess_by_key("0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOp") == ("glm",)
    assert ma.guess_by_key("sk-12345678") == ma._SK_CANDIDATES
    for pid, p in ma.PRESETS.items():
        assert p["base_url"] == "" or p["base_url"].startswith(("https://", "http://")), pid
    assert "deepseek" in ma.do_presets() and "api.deepseek.com" in ma.do_presets()


def test_mask_and_redact():
    assert ma.mask_key(KEY) == "sk-a…1234"
    assert ma.mask_key("short") == "****" and ma.mask_key("") == "(无)"
    red = ma.redact(f"bad key {KEY} and sk-zzzzzzzz-zzzzzzzzzzzz", KEY)
    assert KEY not in red and "sk-zzzzzzzz-zzzzzzzzzzzz" not in red


def test_add_with_provider_fills_url_model_and_probes(env):
    p, up = env
    out = run({"action": "add", "provider": "deepseek", "api_key": KEY})
    assert out.startswith("✓"), out
    m = _models(p)["deepseek-flash"]
    assert m["backend_url"] == "https://api.deepseek.com/v1" and m["api_key"] == KEY
    assert m["supports_vision"] is True and m["enable_thinking"] is True
    assert m["provider"] == "deepseek" and m["api_format"] == "openai-chat"
    assert KEY not in out and "sk-a…1234" in out
    assert {r.url.host for r in up.requests} == {"api.deepseek.com"}
    assert all(r.headers["authorization"] == f"Bearer {KEY}" for r in up.requests)
    # 默认模型没被动
    assert json.loads(p.read_text())["default_model_id"] == "base-model"


def test_add_only_sk_key_detects_provider_via_models(env):
    p, up = env
    up.models_ok_hosts = {"api.moonshot.cn"}           # deepseek 401, kimi 200
    out = run({"action": "add", "api_key": KEY})
    assert out.startswith("✓") and "自动识别" in out, out
    assert "kimi-k2.7-code" in _models(p)


def test_add_custom_id_keeps_wire_name(env):
    p, _ = env
    out = run({"action": "add", "id": "zz-test", "base_url": "http://my.vllm:8000/v1/", "model": "Qwen-X",
               "api_key": KEY, "provider": "vllm"})
    assert out.startswith("✓"), out
    m = _models(p)["zz-test"]
    assert m["served_model_name"] == "Qwen-X" and m["backend_url"] == "http://my.vllm:8000/v1"
    assert m["deployment"] == "vllm"


def test_add_existing_id_refused_unless_overwrite(env):
    """E2E 实测过: 模型把用户起的名字填进 label、id 用了已有的模型名, 结果覆盖了原条目。同 id 必须明确 overwrite。"""
    p, up = env
    before = p.read_text()
    out = run({"action": "add", "id": "spare", "base_url": "http://other.example/v1", "model": "m", "api_key": KEY})
    assert out.startswith("ERROR") and "overwrite" in out and p.read_text() == before and not up.requests
    out = run({"action": "add", "id": "spare", "base_url": "http://other.example/v1", "model": "m", "api_key": KEY,
               "overwrite": True})
    assert out.startswith("✓ 已更新"), out
    assert _models(p)["spare"]["backend_url"] == "http://other.example/v1"


def test_add_failure_does_not_write_and_redacts(env):
    p, up = env
    before = p.read_text()
    up.behavior = "401"
    out = run({"action": "add", "provider": "deepseek", "api_key": KEY})
    assert out.startswith("✗") and "没有写入" in out and "401" in out
    assert KEY not in out
    assert p.read_text() == before


def test_add_model_not_found_lists_available(env):
    p, up = env
    up.behavior, up.models = "404", ["kimi-k3", "kimi-k2.6"]
    out = run({"action": "add", "provider": "kimi", "model": "kimi-nope", "api_key": KEY})
    assert out.startswith("✗") and "kimi-k3" in out and "kimi-nope" not in _models(p)


def test_add_no_vision_no_thinking(env):
    p, up = env
    up.vision = up.thinking = False
    assert run({"action": "add", "provider": "glm", "api_key": KEY}).startswith("✓")
    m = _models(p)["glm-5.3-flash"]
    assert m["supports_vision"] is False and m["enable_thinking"] is False


def test_add_rejects_masked_key_and_unknown_key(env):
    assert run({"action": "add", "provider": "deepseek", "api_key": "••••1234"}).startswith("ERROR")
    assert run({"action": "add", "api_key": "weird-key"}).startswith("ERROR")


def test_list_masks_keys(env):
    out = run({"action": "list"})
    assert "base-model" in out and "spare" in out and "sk-basebase-basebase9999" not in out and "sk-b…9999" in out


def test_remove_guards_default_and_removes_other(env):
    p, _ = env
    out = run({"action": "remove", "id": "base-model"})
    assert out.startswith("ERROR") and "base-model" in _models(p)
    assert run({"action": "remove", "id": "spare"}).startswith("✓") and "spare" not in _models(p)
    assert "未改动" in run({"action": "remove", "id": "spare"})
    assert run({"action": "remove"}).startswith("ERROR")


def test_set_default_updates_model_and_default_id(env, monkeypatch):
    p, _ = env
    monkeypatch.setattr(config, "reload_model", lambda t: None)
    import lib.thinking_adapter as ta
    monkeypatch.setattr(ta, "update", lambda t: None)   # 别改动进程里的全局思考配置, 免得串到别的用例

    class Srv:
        MODEL_ID, BACKEND_URL, API_KEY, MAX_TOKENS = "base-model", "http://base.example/v1", "", 8192
        ENABLE_THINKING, THINKING_BUDGET, CONTEXT_WINDOW, BACKEND_TYPE = False, 0, 32000, "openai"
        WORKSPACE = "/tmp"
    monkeypatch.setattr(mm, "_srv", lambda: Srv)
    monkeypatch.setattr(mm, "HAS_DEEP_SEARCH", False)
    out = run({"action": "set_default", "id": "spare"})
    assert out.startswith("✓"), out
    cfg = json.loads(p.read_text())
    assert cfg["default_model_id"] == "spare" and cfg["model"]["id"] == "spare"
    assert run({"action": "set_default", "id": "nope"}).startswith("ERROR")


def test_test_action(env):
    assert run({"action": "test", "id": "spare"}).startswith("✓")
    assert run({"action": "test", "id": "nope"}).startswith("ERROR")


def test_http_delete_refuses_default(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(mm.router)
    import gateway.model_mgmt as m2
    m2_auth = m2.require_auth
    try:
        m2.require_auth = lambda r: None
        c = TestClient(app)
        assert c.delete("/v1/model/base-model").status_code == 409
        assert c.delete("/v1/model/spare").json()["ok"]
    finally:
        m2.require_auth = m2_auth


def test_save_cfg_falls_back_to_inplace_write_when_rename_busy(env, monkeypatch):
    import errno
    p, _ = env
    ino = p.stat().st_ino

    def busy(*a, **kw):
        raise OSError(errno.EBUSY, "Device or resource busy")
    import lib.atomic_io as aio
    monkeypatch.setattr(aio, "atomic_write_bytes", busy)
    cfg = config.load_cfg()
    cfg["default_model_id"] = "spare"
    config.save_cfg(cfg)
    assert p.stat().st_ino == ino and json.loads(p.read_text())["default_model_id"] == "spare"


def test_tools_live_in_on_demand_groups():
    from lib import tool_groups as tg
    assert tg.TOOL_TO_GROUP["manage_models"] == "settings" and tg.TOOL_TO_GROUP["text_to_speech"] == "voice"
    assert "settings" in tg.detect_user("帮我加一个模型, 我有个 deepseek 的 key sk-xxxx")
    assert "settings" in tg.detect_user("把默认模型换成 kimi")
    assert "voice" in tg.detect_user("把'你好，欢迎使用 LiteCode'用晓晓的声音读出来")
    assert tg.detect_user("写一份市场部工作总结") == set()
