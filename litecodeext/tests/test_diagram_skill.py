"""skills/diagram: 类型判断、PlantUML 编码 (纯逻辑, 不联网); 有 playwright 时实渲染 mermaid / dot。"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "diagram" / "scripts" / "diagram.py"
spec = importlib.util.spec_from_file_location("diagram", SCRIPT)
dg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dg)


def test_guess_type():
    assert dg.guess_type("x", "a.puml") == "plantuml"
    assert dg.guess_type("x", "a.gv") == "dot"
    assert dg.guess_type("@startuml\nA->B\n@enduml", "-") == "plantuml"
    assert dg.guess_type("digraph G { a -> b }", "-") == "dot"
    assert dg.guess_type("flowchart LR\n A-->B", "-") == "mermaid"


def test_plantuml_encode_matches_official_example():
    # PlantUML 官方文档示例: "Bob -> Alice : hello" 的编码
    assert dg.plantuml_encode("Bob -> Alice : hello") == "SyfFKj2rKt3CoKnELR1Io4ZDoSa70000"


def test_assets_present():
    assert (dg.ASSETS / "mermaid.min.js").stat().st_size > 1_000_000
    assert (dg.ASSETS / "viz-standalone.js").stat().st_size > 1_000_000


@pytest.mark.parametrize("src,kind", [("flowchart LR\n  A[开始] --> B[结束]\n", "mermaid"),
                                      ('digraph G { "网页" -> "网关" }', "dot")])
def test_render_png(tmp_path, src, kind):
    pytest.importorskip("playwright.sync_api")
    out = tmp_path / "o.png"
    r = subprocess.run([sys.executable, str(SCRIPT), "render", "-", "--type", kind, "-o", str(out)],
                       input=src, capture_output=True, text=True, timeout=120)
    if "Executable doesn't exist" in r.stderr:
        pytest.skip("没有 playwright chromium")
    assert r.returncode == 0, r.stderr
    assert out.read_bytes()[:4] == b"\x89PNG"
