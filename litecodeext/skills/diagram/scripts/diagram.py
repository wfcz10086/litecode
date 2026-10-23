#!/usr/bin/env python3
"""diagram.py — 把 Mermaid / Graphviz(dot) / PlantUML 源码渲染成 PNG / SVG。

Mermaid 和 Graphviz 用本机已有的 Playwright Chromium + 技能自带的 JS 库 (assets/) 渲染, 离线可用、不用装系统包;
PlantUML 需要 Java: 本机没有时 `setup-plantuml` 一键装 (国内源), 或设置 PLANTUML_SERVER 用自建的 PlantUML 服务。

用法:
  diagram.py render 图.mmd -o 图.png           # 按扩展名判断类型: .mmd/.mermaid · .dot/.gv · .puml/.plantuml/.uml
  diagram.py render 图.txt --type mermaid -o 图.svg --theme forest --scale 2
  diagram.py render - --type dot -o 图.png < a.dot   # 从标准输入读
  diagram.py setup-plantuml                     # 装 Java + plantuml.jar (约 1 分钟)
  diagram.py check                              # 看哪些引擎可用
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
PLANTUML_VER = "1.2025.10"
PLANTUML_JAR = Path(os.environ.get("PLANTUML_JAR") or
                    Path.home() / ".cache" / "litecode" / "plantuml" / f"plantuml-{PLANTUML_VER}.jar")
PLANTUML_URLS = [
    f"https://maven.aliyun.com/repository/public/net/sourceforge/plantuml/plantuml/{PLANTUML_VER}/plantuml-{PLANTUML_VER}.jar",
    f"https://repo1.maven.org/maven2/net/sourceforge/plantuml/plantuml/{PLANTUML_VER}/plantuml-{PLANTUML_VER}.jar",
]
EXT_TYPES = {".mmd": "mermaid", ".mermaid": "mermaid", ".dot": "dot", ".gv": "dot",
             ".puml": "plantuml", ".plantuml": "plantuml", ".uml": "plantuml", ".pu": "plantuml"}
CJK_FONT = "'Noto Sans CJK SC','Noto Sans SC','Microsoft YaHei','PingFang SC',sans-serif"


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def guess_type(src: str, path: str) -> str:
    t = EXT_TYPES.get(Path(path).suffix.lower()) if path != "-" else None
    if t:
        return t
    s = src.lstrip()
    if s.startswith("@start"):
        return "plantuml"
    if s.split(None, 1)[0:1] and s.split(None, 1)[0] in ("digraph", "graph", "strict"):
        return "dot"
    return "mermaid"


# ── Mermaid / Graphviz: 浏览器里渲染 ─────────────────────────────
_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:%(bg)s} #out{display:inline-block;padding:16px;font-family:%(font)s}
#out svg{max-width:none!important}</style></head><body><div id="out"></div></body></html>"""

_JS_MERMAID = """async ([code, theme]) => {
  mermaid.initialize({startOnLoad:false, theme, securityLevel:'strict',
                      fontFamily:%s, flowchart:{htmlLabels:true}});
  const {svg} = await mermaid.render('d' + Date.now(), code);
  document.getElementById('out').innerHTML = svg;
  return svg;
}""" % json.dumps(CJK_FONT)

_JS_DOT = """async ([code]) => {
  const viz = await Viz.instance();
  const el = viz.renderSVGElement(code);
  document.getElementById('out').appendChild(el);
  return new XMLSerializer().serializeToString(el);
}"""


def render_browser(kind: str, code: str, out: Path, theme: str, scale: float, bg: str) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("缺 Python playwright (pip install playwright && playwright install chromium)")
    lib = ASSETS / ("mermaid.min.js" if kind == "mermaid" else "viz-standalone.js")
    if not lib.exists():
        die(f"缺渲染库 {lib}")
    if kind == "dot" and "fontname" not in code:
        # 中文默认字体: 不指定时 Graphviz 用 Times, 中文会变方块
        code = code.replace("{", '{\n  graph [fontname="Noto Sans CJK SC"]; node [fontname="Noto Sans CJK SC"]; '
                                  'edge [fontname="Noto Sans CJK SC"];', 1)
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = b.new_page(device_scale_factor=scale)
            page.set_content(_PAGE % {"bg": bg, "font": CJK_FONT})
            page.add_script_tag(path=str(lib))
            try:
                svg = page.evaluate(_JS_MERMAID if kind == "mermaid" else _JS_DOT,
                                    [code, theme] if kind == "mermaid" else [code])
            except Exception as e:  # 语法错误: 把渲染库的报错原样带回, 方便改源码
                msg = "\n".join(str(e).replace("Page.evaluate: ", "").splitlines()[:4])
                die(f"{kind} 语法/渲染错误:\n{msg[:800]}", 2)
            if out.suffix.lower() == ".svg":
                out.write_text(svg, encoding="utf-8")
            else:
                # 按量出的区域截图: locator.screenshot 要等"元素稳定", 有的机器上一直等不到 (2026-10-23 主机上必现超时)
                box = page.evaluate("""() => { const r = document.getElementById('out').getBoundingClientRect();
                    return {x: r.x, y: r.y, width: Math.ceil(r.width), height: Math.ceil(r.height)}; }""")
                page.set_viewport_size({"width": max(800, int(box["x"] + box["width"]) + 8),
                                        "height": max(600, int(box["y"] + box["height"]) + 8)})
                page.screenshot(path=str(out), clip=box, omit_background=(bg == "transparent"), timeout=120000)
        finally:
            b.close()


# ── PlantUML ─────────────────────────────────────────────────────
_P64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"


def plantuml_encode(text: str) -> str:
    """PlantUML 服务的 URL 编码: raw deflate + 自定义 base64 字母表。"""
    data = zlib.compress(text.encode("utf-8"), 9)[2:-4]
    data += b"\0" * (-len(data) % 3)   # 官方实现按 3 字节一组, 不足补 0 (不是 base64 的 = 填充)
    std = base64.b64encode(data).decode()
    table = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", _P64)
    return std.translate(table)


def render_plantuml(code: str, out: Path) -> None:
    fmt = "svg" if out.suffix.lower() == ".svg" else "png"
    if not code.lstrip().startswith("@start"):
        code = "@startuml\n" + code.strip() + "\n@enduml\n"
    java = shutil.which("java")
    if java and PLANTUML_JAR.exists():
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "diagram.puml"
            src.write_text(code, encoding="utf-8")
            # smetana: 内置布局引擎, 不依赖 Graphviz
            r = subprocess.run([java, "-Djava.awt.headless=true", "-jar", str(PLANTUML_JAR), f"-t{fmt}",
                                "-charset", "UTF-8", "-Playout=smetana", str(src)],
                               capture_output=True, text=True, timeout=120)
            res = src.with_suffix("." + fmt)
            if not res.exists():
                die(f"plantuml 渲染失败: {(r.stderr or r.stdout).strip()[:800]}", 2)
            shutil.move(str(res), out)
            if r.returncode != 0:   # 语法错误时 PlantUML 仍会输出一张错误图, 同时返回非 0
                die(f"plantuml 语法错误 (错误图已写到 {out}): {(r.stderr or r.stdout).strip()[:800]}", 2)
        return
    server = os.environ.get("PLANTUML_SERVER", "").rstrip("/")
    if server:
        url = f"{server}/{fmt}/{plantuml_encode(code)}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            out.write_bytes(resp.read())
        return
    die("本机没有 PlantUML: 运行 `diagram.py setup-plantuml` 安装 (Java + jar, 约 1 分钟), "
        "或设置 PLANTUML_SERVER=自建服务地址; 也可以把图改写成 Mermaid (时序图/类图/状态图/ER 图都支持)。", 3)


def setup_plantuml() -> int:
    if not shutil.which("java"):
        if os.geteuid() != 0:
            die("没有 java, 且不是 root, 装不了: 请用 root 执行 apt-get install -y default-jre-headless")
        print("安装 Java (default-jre-headless)…", flush=True)
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        subprocess.run(["apt-get", "update", "-qq"], check=False, env=env)
        r = subprocess.run(["apt-get", "install", "-y", "-qq", "--no-install-recommends", "default-jre-headless"], env=env)
        if r.returncode != 0 or not shutil.which("java"):
            die("Java 安装失败")
    if not PLANTUML_JAR.exists():
        PLANTUML_JAR.parent.mkdir(parents=True, exist_ok=True)
        for u in PLANTUML_URLS:
            try:
                print(f"下载 {u}", flush=True)
                tmp = PLANTUML_JAR.with_suffix(".part")
                urllib.request.urlretrieve(u, tmp)
                if tmp.stat().st_size > 5_000_000:
                    tmp.rename(PLANTUML_JAR)
                    break
            except Exception as e:
                print(f"  失败: {e}", flush=True)
        if not PLANTUML_JAR.exists():
            die("plantuml.jar 下载失败")
    print(f"OK PlantUML 就绪: {PLANTUML_JAR}")
    return 0


def check() -> int:
    try:
        import playwright  # noqa: F401
        pw = "✓"
    except ImportError:
        pw = "✗ (缺 playwright)"
    print(f"mermaid   {pw if (ASSETS / 'mermaid.min.js').exists() else '✗ 缺 assets/mermaid.min.js'}")
    print(f"graphviz  {pw if (ASSETS / 'viz-standalone.js').exists() else '✗ 缺 assets/viz-standalone.js'}")
    if shutil.which("java") and PLANTUML_JAR.exists():
        print("plantuml  ✓ 本机 Java")
    elif os.environ.get("PLANTUML_SERVER"):
        print(f"plantuml  ✓ 服务 {os.environ['PLANTUML_SERVER']}")
    else:
        print("plantuml  ✗ 运行 setup-plantuml 安装")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("src", help="源码文件, - 表示标准输入")
    r.add_argument("-o", "--out", required=True, help="输出 .png 或 .svg")
    r.add_argument("--type", choices=["mermaid", "dot", "plantuml"])
    r.add_argument("--theme", default="default", help="mermaid 主题: default/forest/dark/neutral/base")
    r.add_argument("--scale", type=float, default=2.0, help="PNG 清晰度倍数")
    r.add_argument("--bg", default="white", help="背景色, transparent=透明")
    sub.add_parser("setup-plantuml")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    if a.cmd == "setup-plantuml":
        return setup_plantuml()
    if a.cmd == "check":
        return check()
    code = sys.stdin.read() if a.src == "-" else Path(a.src).read_text(encoding="utf-8")
    if not code.strip():
        die("源码是空的")
    kind = a.type or guess_type(code, a.src)
    out = Path(a.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if kind == "plantuml":
        render_plantuml(code, out)
    else:
        render_browser(kind, code, out, a.theme, a.scale, a.bg)
    print(f"OK {kind} → {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
