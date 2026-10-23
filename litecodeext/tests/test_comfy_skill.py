"""comfyui-image / comfyui-video 技能: comfy.py 的改参 / probe 解析 / 网页格式转换 / 端到端提交下载 (假 ComfyUI)。"""
import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SK = ROOT / "skills"
sys.path.insert(0, str(SK / "comfyui-image" / "scripts"))
import comfy  # noqa: E402

OI = {
    "UNETLoader": {"input": {"required": {"unet_name": [["qwen_image_2.1_int8_convrot.safetensors",
                                                         "qwen_image_2.1_bf16.safetensors"], {}],
                                          "weight_dtype": [["default", "fp8"], {}]}}},
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": ["COMBO", {"options": ["dreamshaper_8.safetensors"]}]}}},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}], "clip": ["CLIP", {}]}}},
    "KSampler": {"input": {"required": {"model": ["MODEL", {}], "seed": ["INT", {"control_after_generate": True}],
                                        "steps": ["INT", {}], "cfg": ["FLOAT", {}],
                                        "sampler_name": [["euler", "dpmpp_2m"], {}], "scheduler": [["simple", "karras"], {}],
                                        "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}],
                                        "latent_image": ["LATENT", {}], "denoise": ["FLOAT", {}]}}},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}], "batch_size": ["INT", {}]}}},
    "VAEDecode": {"input": {"required": {"samples": ["LATENT", {}], "vae": ["VAE", {}]}}},
    "SaveImage": {"input": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {}]}}},
    "LoadImage": {"input": {"required": {"image": [["a.png"], {"image_upload": True}]}}},
    "LoadImageFromURL": {"input": {"required": {"url": ["STRING", {}]}}},
    "KlingApi": {"input": {"required": {"model_name": [["kling-v2"], {}]}}},
}

SD = {
    "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
    "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ckpt", 1], "text": "cat"}},
    "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ckpt", 1], "text": ""}},
    "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "ks": {"class_type": "KSampler", "inputs": {"model": ["ckpt", 0], "positive": ["pos", 0], "negative": ["neg", 0],
                                                "latent_image": ["latent", 0], "seed": 1, "steps": 20, "cfg": 7.0,
                                                "sampler_name": "euler", "scheduler": "karras", "denoise": 1.0}},
    "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}},
    "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": "x"}},
}


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("COMFY_SSH_ROUTES", raising=False)
    monkeypatch.delenv("COMFY_ENDPOINTS_FILE", raising=False)


# ---------------- 纯函数 ----------------

def test_heuristic_params_follow_sampler_links():
    g = json.loads(json.dumps(SD))
    done = comfy.apply_params(g, {}, {"prompt": "dog", "negative": "blurry", "seed": 7, "width": 768, "steps": 9})
    assert g["pos"]["inputs"]["text"] == "dog" and g["neg"]["inputs"]["text"] == "blurry"
    assert g["ks"]["inputs"]["seed"] == 7 and g["ks"]["inputs"]["steps"] == 9
    assert g["latent"]["inputs"]["width"] == 768 and any("latent.width" in d for d in done)
    with pytest.raises(comfy.ComfyError):
        comfy.apply_params(g, {}, {"frames": 22})          # 图片工作流没有帧数字段 → 明确报错


def test_set_field_keeps_types_and_resolves_class_name():
    g = json.loads(json.dumps(SD))
    assert comfy.set_field(g, "KSampler.steps", "12", raw=True) == "ks.steps"
    assert g["ks"]["inputs"]["steps"] == 12
    comfy.set_field(g, "pos.text", "123", raw=True)
    assert g["pos"]["inputs"]["text"] == "123"              # 原来是字符串就保持字符串
    with pytest.raises(comfy.ComfyError):
        comfy.set_field(g, "CLIPTextEncode.text", "x", raw=True)   # 同类型多个节点要写 id


def test_drop_node_removes_links():
    g = json.loads(json.dumps(SD))
    comfy.drop_node(g, "neg")
    assert "neg" not in g and "negative" not in g["ks"]["inputs"]


def test_model_catalog_and_fix_models():
    cat = comfy.model_catalog(OI)
    assert cat["unet_name"] == ["qwen_image_2.1_bf16.safetensors", "qwen_image_2.1_int8_convrot.safetensors"]
    assert "model_name" not in cat                          # 云 API 型号名不算模型
    assert cat["ckpt_name"] == ["dreamshaper_8.safetensors"]  # 新版 ["COMBO", {"options": [...]}] 格式
    g = {"u": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2.1_fp8.safetensors"}}}
    changes, problems = comfy.fix_models(g, OI)
    assert not problems and g["u"]["inputs"]["unet_name"].startswith("qwen_image_2.1_")
    g = json.loads(json.dumps(SD))
    _, problems = comfy.fix_models(g, OI)                   # sdxl ≠ dreamshaper: 不硬套, 报缺什么
    assert problems and "缺模型 sd_xl_base_1.0" in problems[0] and "dreamshaper_8" in problems[0]
    changes, problems = comfy.fix_models(g, OI, any_fields=("ckpt.ckpt_name",))
    assert not problems and g["ckpt"]["inputs"]["ckpt_name"] == "dreamshaper_8.safetensors"
    g = {"i": {"class_type": "LoadImage", "inputs": {"image": "litecode/new.png"}}}
    assert comfy.fix_models(g, OI) == ([], [])              # 素材槽不校验


def test_templates_are_consistent():
    ts = {t["name"]: t for t in comfy.list_templates()}
    assert {"qwen21_t2i", "qwen21_edit", "qwen21_multi", "sd_checkpoint_t2i",
            "h3_t2v", "h3_i2v", "h3_fl2v", "h3_r2v"} <= set(ts)
    for t in ts.values():
        g = t["graph"]
        for key, targets in (t.get("params") or {}).items():
            assert key in comfy.PARAM_KEYS + ("resolution",), (t["name"], key)
            for tgt in targets:
                nid, f = tgt.split(".", 1)
                assert f in g[nid]["inputs"], (t["name"], tgt)
        for slot, tgt in (t.get("inputs") or {}).items():
            nid, f = tgt.split(".", 1)
            assert f in g[nid]["inputs"], (t["name"], slot)
        for nodes in (t.get("optional_inputs") or {}).values():
            assert all(n in g for n in nodes)
        txt = json.dumps(g, ensure_ascii=False)
        assert ("bing" + "te") not in txt and "10.128." not in txt  # 模板不绑定任何具体机器
    h3 = ts["h3_r2v"]
    assert h3["defaults"]["steps"] == 10 and (h3["defaults"]["frames"] - 5) % 17 == 0
    assert h3["defaults"]["width"] % 32 == 0 and h3["defaults"]["height"] % 32 == 0
    ok, why = comfy.template_fit(ts["qwen21_t2i"]["graph"], ts["qwen21_t2i"], OI)
    assert not ok and "缺节点" in why[0]


def test_ui_to_api_conversion():
    ui = {"nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "mode": 0, "inputs": [], "widgets_values": ["dreamshaper_8.safetensors"]},
        {"id": 2, "type": "CLIPTextEncode", "mode": 0, "inputs": [{"name": "clip", "type": "CLIP", "link": 11}],
         "widgets_values": ["a cat"]},
        {"id": 3, "type": "Reroute", "mode": 0, "inputs": [{"name": "", "type": "*", "link": 12}]},
        {"id": 4, "type": "KSampler", "mode": 0,
         "inputs": [{"name": "model", "type": "MODEL", "link": 13}, {"name": "positive", "type": "CONDITIONING", "link": 14},
                    {"name": "seed", "type": "INT", "link": None, "widget": {"name": "seed"}},
                    {"name": "steps", "type": "INT", "link": 15, "widget": {"name": "steps"}},
                    {"name": "cfg", "type": "FLOAT", "link": None, "widget": {"name": "cfg"}},
                    {"name": "sampler_name", "type": "COMBO", "link": None, "widget": {"name": "sampler_name"}},
                    {"name": "scheduler", "type": "COMBO", "link": None, "widget": {"name": "scheduler"}},
                    {"name": "denoise", "type": "FLOAT", "link": None, "widget": {"name": "denoise"}}],
         "widgets_values": [42, "randomize", 20, 7.5, "euler", "karras", 1.0]},
        {"id": 5, "type": "PrimitiveNode", "mode": 0, "inputs": [], "widgets_values": [30]},
        {"id": 6, "type": "Note", "mode": 0, "inputs": [], "widgets_values": ["说明"]},
        {"id": 7, "type": "LoadImage", "mode": 4, "inputs": [], "widgets_values": ["a.png"]},
    ], "links": [[11, 1, 1, 2, 0, "CLIP"], [12, 1, 0, 3, 0, "MODEL"], [13, 3, 0, 4, 0, "MODEL"],
                 [14, 2, 0, 4, 1, "CONDITIONING"], [15, 5, 0, 4, 3, "INT"]]}
    api = comfy.ui_to_api(ui, OI)
    assert set(api) == {"1", "2", "4"}
    ks = api["4"]["inputs"]
    assert ks["seed"] == 42 and ks["cfg"] == 7.5 and ks["sampler_name"] == "euler"   # 跳过 randomize
    assert ks["steps"] == 30                               # Primitive 的值
    assert ks["model"] == ["1", 0]                          # 穿过 Reroute
    assert api["2"]["inputs"] == {"text": "a cat", "clip": ["1", 1]}
    with pytest.raises(comfy.ComfyError, match="子图"):
        comfy.ui_to_api({"nodes": [{"id": 1, "type": "abc-uuid"}], "links": [],
                         "definitions": {"subgraphs": [{"id": "abc-uuid"}]}}, OI)


def test_outputs_timing_gpu_name():
    hist = {"outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "s", "type": "output"},
                                         {"filename": "p.png", "subfolder": "", "type": "temp"}]},
                        "10": {"gifs": [{"filename": "v.mp4", "subfolder": "video", "type": "output"}]}},
            "status": {"status_str": "success", "messages": [
                ["execution_start", {"timestamp": 1000}], ["execution_cached", {"nodes": ["ks"]}],
                ["execution_success", {"timestamp": 47500}]]}}
    outs = comfy.collect_outputs(hist)
    assert [o["filename"] for o in outs] == ["a.png", "v.mp4"]
    tm = comfy.exec_timing(hist)
    assert tm["render_s"] == 46.5 and tm["cached"] == ["ks"]
    assert comfy.gpu_name("cuda:0 NVIDIA RTX 4500 Ada Generation : cudaMallocAsync") == "NVIDIA RTX 4500 Ada Generation"


def test_ssh_cmd_bypasses_bad_owner_config(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("Host other\n  Port 1\nHost jump jump2\n  HostName localhost\n  Port 38003\n  User ssss\n"
                   "  IdentityFile ~/.ssh/id_ed25519\n")
    cfg.chmod(0o600)
    assert comfy.ssh_cmd("jump", cfg)[-1] == "jump"         # 属主正常: 直接用别名
    cfg.chmod(0o666)                                        # 模拟 "Bad owner or permissions"
    cmd = comfy.ssh_cmd("jump", cfg)
    assert cmd[1:3] == ["-F", "/dev/null"] and cmd[-1] == "ssss@localhost"
    assert cmd[cmd.index("-p") + 1] == "38003" and "-i" in cmd
    cmd = comfy.ssh_cmd("ubuntu@127.0.0.1:38111", cfg)      # 直接写 user@host:port (LiteCode 主机库的别名系统 ssh 不认识)
    assert cmd[1:3] == ["-F", "/dev/null"] and cmd[-1] == "ubuntu@127.0.0.1" and cmd[cmd.index("-p") + 1] == "38111"
    assert comfy.ssh_cmd("10.0.0.9:2222", cfg)[-3:] == ["-p", "2222", "10.0.0.9"]


def test_env_route_and_no_builtin_hosts(monkeypatch):
    assert comfy.env_route("http://10.1.2.3:8188") is None   # 代码里没有任何默认跳板
    monkeypatch.setenv("COMFY_SSH_ROUTES", "10.1.2.*=jump, 192.168.0.5:80=b")
    assert comfy.env_route("10.1.2.3:8188") == "jump" and comfy.env_route("192.168.0.5") == "b"
    src = (SK / "comfyui-image" / "scripts" / "comfy.py").read_text(encoding="utf-8")
    assert "10.128." not in src and "gpu-a" not in src and "113.44" not in src


def test_endpoint_memory():
    with pytest.raises(comfy.ComfyError, match="先问用户"):
        comfy.resolve_url(None)
    comfy.store_update("10.0.0.1:8188", via="jump")
    comfy.store_update("10.0.0.2:8188")
    assert comfy.resolve_url(None) in ("http://10.0.0.1:8188", "http://10.0.0.2:8188")
    assert comfy.store_get("http://10.0.0.1:8188")["via"] == "jump"
    assert comfy.main(["remember", "10.0.0.1:8188", "--wait-idle", "no", "--via", "none"]) == 0
    rec = comfy.store_get("10.0.0.1:8188")
    assert rec["wait_idle"] is False and rec["via"] == ""


def test_skill_files_and_aliases():
    sys.path[:0] = [str(ROOT), str(ROOT / "core")]
    from lib import skills
    skills._build_skill_index()
    for msg, name in (("用 comfyui 在 198.51.100.16:8488 画一只橘猫", "comfyui-image"),
                      ("帮我文生图一张赛博朋克城市", "comfyui-image"),
                      ("用这两张图做个首尾帧视频", "comfyui-video"),
                      ("图生视频: 让这张照片动起来", "comfyui-video")):
        assert f"[Active Skill: {name}]" in skills.auto_load_skills(msg), msg
    for d in ("comfyui-image", "comfyui-video"):
        txt = (SK / d / "SKILL.md").read_text(encoding="utf-8")
        assert skills._frontmatter(txt)["name"] == d and "需要一个 ComfyUI 地址" in txt
        assert "10.128." not in txt and "gpu-a" not in txt  # 具体机器只在 references/ 里
        assert len(txt) < 10000
    ex = (ROOT.parent / "scripts" / "release_exclude.txt").read_text(encoding="utf-8")
    assert "litecodeext/skills/comfyui-image/references/" in ex


# ---------------- 端到端: 假 ComfyUI ----------------

class FakeComfy(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st, u = self.state, urllib.parse.urlparse(self.path)
        if u.path == "/system_stats":
            return self._send({"system": {"comfyui_version": "0.9.9"},
                               "devices": [{"name": "cuda:0 Fake GPU : native", "vram_total": 2**34, "vram_free": 2**33}]})
        if u.path == "/queue":
            st["queue_polls"] = st.get("queue_polls", 0) + 1
            busy = st.get("busy_polls", 0) >= st["queue_polls"]
            return self._send({"queue_running": [[0, "other", {}, {}, []]] if busy else [], "queue_pending": []})
        if u.path == "/object_info":
            return self._send(OI)
        if u.path == "/api/userdata":
            return self._send(["mine.json"])
        if u.path.startswith("/history/"):
            pid = u.path.rsplit("/", 1)[1]
            return self._send({pid: {"status": {"status_str": "success", "completed": True, "messages": [
                ["execution_start", {"timestamp": 1000}], ["execution_success", {"timestamp": 3000}]]},
                "outputs": {"save": {"images": [{"filename": "out_00001_.png", "subfolder": "litecode", "type": "output"}]}}}}
                if pid in st.get("prompts", {}) else {})
        if u.path.startswith("/history"):
            return self._send({})
        if u.path == "/view":
            st.setdefault("views", []).append(urllib.parse.parse_qs(u.query))
            return self._send(b"\x89PNG fake", "image/png")
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        if self.path == "/upload/image":
            self.state.setdefault("uploads", []).append(body)
            name = body.split(b'filename="')[1].split(b'"')[0].decode()
            return self._send({"name": name, "subfolder": "litecode", "type": "input"})
        if self.path == "/prompt":
            req = json.loads(body)
            self.state.setdefault("prompts", {})["pid-1"] = req["prompt"]
            return self._send({"prompt_id": "pid-1", "number": 1, "node_errors": {}})
        self.send_error(404)


@pytest.fixture
def server():
    FakeComfy.state = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}", FakeComfy.state
    srv.shutdown()


def test_probe_end_to_end(server, capsys):
    url, _ = server
    assert comfy.main(["probe", url]) == 0
    out = capsys.readouterr().out
    assert "Fake GPU" in out and "unet_name (2)" in out and "mine.json" in out
    assert "✓ sd_checkpoint_t2i" in out and "✗ h3_t2v" in out and "✗ qwen21_t2i" in out
    rec = comfy.store_get(url)
    assert rec["probe"]["templates_ok"] == ["sd_checkpoint_t2i"] and rec["wait_idle"] is True


def test_run_end_to_end_with_upload_and_wait_idle(server, tmp_path, monkeypatch, capsys):
    url, st = server
    st["busy_polls"] = 1                                    # 第一次查队列有别人的任务 → 等它空了再提交
    monkeypatch.setattr(comfy.time, "sleep", lambda s: None)
    wf = tmp_path / "wf.json"
    g = json.loads(json.dumps(SD))
    g["img"] = {"class_type": "LoadImageFromURL", "inputs": {"url": ""}}
    wf.write_text(json.dumps(g))
    pic = tmp_path / "in.png"
    pic.write_bytes(b"\x89PNG input")
    out = tmp_path / "out"
    rc = comfy.main(["run", url, "-w", str(wf), "--prompt", "一只橘猫", "--seed", "5", "--input", f"image={pic}",
                     "--set", "ckpt.ckpt_name=dreamshaper_8.safetensors", "-o", str(out)])
    log = capsys.readouterr().out
    assert rc == 0, log
    sent = st["prompts"]["pid-1"]
    assert sent["pos"]["inputs"]["text"] == "一只橘猫" and sent["ks"]["inputs"]["seed"] == 5
    assert sent["img"]["inputs"]["url"].startswith(f"http://{url}/view?filename=lc_")   # 服务器自己的 /view 链接
    assert st["uploads"] and st["queue_polls"] >= 2
    files = list(out.glob("*.png"))
    assert len(files) == 1 and files[0].read_bytes() == b"\x89PNG fake"
    res = json.loads(log.split("RESULT ", 1)[1].splitlines()[0])
    assert res["render_s"] == 2.0 and res["files"][0]["path"] == str(files[0])
    assert comfy.store_get(url)["last_ok"]["workflow"] == str(wf)


def test_run_refuses_missing_model_and_respects_idle_timeout(server, tmp_path, monkeypatch, capsys):
    url, st = server
    wf = tmp_path / "wf.json"
    wf.write_text(json.dumps(SD))
    assert comfy.main(["run", url, "-w", str(wf)]) == 1     # sdxl 不在服务器上 → 不提交
    assert "缺模型" in capsys.readouterr().out and "prompts" not in st
    st["busy_polls"] = 10**6
    monkeypatch.setattr(comfy.time, "sleep", lambda s: None)
    rc = comfy.main(["run", url, "-w", "sd_checkpoint_t2i", "--idle-timeout", "0"])
    assert rc == 3 and "prompts" not in st                  # 线上机一直忙: 不插队
