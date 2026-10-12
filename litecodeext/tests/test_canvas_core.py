"""画布分屏纯逻辑 (web_assets/canvas_core.js): 用 node 直接跑, 没装 node 时跳过。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parent.parent / "web_assets" / "canvas_core.js"
NODE = shutil.which("node") or ""
pytestmark = pytest.mark.skipif(not NODE, reason="node 不可用")


def js(expr: str):
    src = f"const C = require({json.dumps(str(CORE))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def lines(n, prefix="x = 1"):
    return "\n".join(f"{prefix}  # {i}" for i in range(n))


def test_lang_from_class():
    assert js('C.langOf("hljs language-python")') == "python"
    assert js('C.langOf("language-c++")') == "c++"
    assert js('C.langOf("")') == ""


def test_short_code_stays_inline_long_code_goes_to_canvas():
    assert js(f"C.shouldCanvas('python', {json.dumps(lines(19))})") is False
    assert js(f"C.shouldCanvas('python', {json.dumps(lines(20))})") is True
    assert js(f"C.shouldCanvas('json', {json.dumps('[' + '1,' * 800 + '1]')})") is True


def test_html_and_svg_have_lower_threshold_and_are_sniffed():
    page = "<!DOCTYPE html>\n<html>\n<head><title>贪吃蛇</title></head>\n" + "<p>x</p>\n" * 6
    assert js(f"C.kindOf('', {json.dumps(page)})") == "html"
    assert js(f"C.shouldCanvas('', {json.dumps(page)})") is True
    svg = "<svg viewBox='0 0 10 10'>\n" + "<rect/>\n" * 8 + "</svg>"
    assert js(f"C.kindOf('xml', {json.dumps(svg)})") == "svg"
    assert js(f"C.kindOf('xml', {json.dumps('<root/>')})") == "code"


def test_title_prefers_html_title_then_filename_comment():
    page = "<html><head><title>贪吃蛇</title></head></html>"
    assert js(f"C.titleOf('html', {json.dumps(page)})") == "贪吃蛇"
    src = "#!/usr/bin/env python3\n# app/server.py — 入口\nimport os"
    assert js(f"C.titleOf('python', {json.dumps(src)})") == "server.py"
    assert js("C.titleOf('python', '# version 1.2.3\\nx=1')") == "PYTHON 代码"
    assert js("C.titleOf('', 'x')") == "代码"


def test_download_name():
    assert js("C.fileName('python', '# utils.py\\nx=1')") == "utils.py"
    assert js("C.fileName('python', 'x=1')") == "canvas.py"
    assert js("C.fileName('', '<!doctype html><html></html>')") == "canvas.html"
    assert js("C.fileName('dockerfile', 'FROM x')") == "Dockerfile"
    assert js("C.fileName('weird', 'x')") == "canvas.txt"
