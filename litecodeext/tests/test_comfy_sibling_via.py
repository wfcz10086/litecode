"""同网段已记住跳板自动复用 (2026-10-22: 165 不用先失败一次再让模型翻主机库)。"""
import importlib.util
import json
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "skills/comfyui-image/scripts/comfy.py"


def test_sibling_via(tmp_path, monkeypatch):
    f = tmp_path / "ep.json"
    f.write_text(json.dumps({"http://198.51.100.16:8488": {"url": "http://198.51.100.16:8488", "via": "u@j:22"},
                             "http://10.9.0.5:8188": {"url": "http://10.9.0.5:8188", "via": "x@y:22"}}))
    monkeypatch.setenv("COMFY_ENDPOINTS_FILE", str(f))
    spec = importlib.util.spec_from_file_location("comfy_t", P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.sibling_via("198.51.100.16") == "u@j:22"
    assert m.sibling_via("10.128.62.1") is None
    assert m.sibling_via("comfy.local") is None
