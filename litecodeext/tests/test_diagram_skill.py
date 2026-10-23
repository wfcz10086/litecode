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


def _can_screenshot() -> bool:
    """当前机器的 headless chromium 能不能截图 (2026-10-23 宿主机出现过连空白页截图都卡死, 容器里正常)。"""
    code = ("from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            " b=p.chromium.launch(args=['--no-sandbox']); pg=b.new_page(); pg.set_content('<b>x</b>')\n"
            " pg.screenshot(timeout=15000); b.close()")
    try:
        return subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=40).returncode == 0
    except subprocess.TimeoutExpired:
        return False


@pytest.mark.parametrize("src,kind", [("flowchart LR\n  A[开始] --> B[结束]\n", "mermaid"),
                                      ('digraph G { "网页" -> "网关" }', "dot")])
def test_render_png(tmp_path, src, kind):
    pytest.importorskip("playwright.sync_api")
    if not _can_screenshot():
        pytest.skip("本机 headless chromium 无法截图 (环境问题, 与画图代码无关)")
    out = tmp_path / "o.png"
    r = subprocess.run([sys.executable, str(SCRIPT), "render", "-", "--type", kind, "-o", str(out)],
                       input=src, capture_output=True, text=True, timeout=120)
    if "Executable doesn't exist" in r.stderr:
        pytest.skip("没有 playwright chromium")
    assert r.returncode == 0, r.stderr
    assert out.read_bytes()[:4] == b"\x89PNG"
