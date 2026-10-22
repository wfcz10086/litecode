#!/usr/bin/env python3
"""render.py — HTML → PDF / 每页 PNG / 长图 (playwright chromium, 单例, 用完即关)。

用法:
  python3 render.py deck.html --pdf deck.pdf                 # 幻灯片 16:9 (1280×720) / 文档 A4, 自动判断
  python3 render.py deck.html --png shots/                   # 每页一张 PNG: shots/page-01.png ...
  python3 render.py deck.html --long deck_long.png           # 长图 (各页竖向拼接, 适合手机分享)
  python3 render.py doc.html --pdf doc.pdf --png shots/ --kind doc --scale 2

判断规则: 有 section.slide → slides; 有 section.page → doc (固定 A4 页); 都没有 → flow (流式文档, PDF 按 A4 自动分页)。
输出: stdout 打印一行 JSON {"kind", "pages", "pdf", "png": [...], "long"}。
"""
from __future__ import annotations

import argparse
from _ds import safe_screenshot
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ds  # noqa: E402

PRINT_FIX = """
@media print {
  html, body { background: none !important; margin: 0 !important; padding: 0 !important; }
  section.slide, section.page { margin: 0 !important; box-shadow: none !important;
    page-break-after: always; break-after: page; }
  section.slide:last-of-type, section.page:last-of-type { page-break-after: auto; break-after: auto; }
}"""


def page_rects(page) -> list[dict]:
    return page.evaluate("""(sel) => Array.from(document.querySelectorAll(sel)).map(s => {
        const r = s.getBoundingClientRect();
        return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height};
    })""", _ds.PAGE_SELECTOR)


def shoot_pages(page, out_dir: Path, prefix: str = "page") -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    rects = page_rects(page)
    if not rects:                                 # 流式文档: 整页长截图当一页
        p = out_dir / f"{prefix}-01.png"
        safe_screenshot(page, path=str(p), full_page=True)
        return [str(p)]
    for i, r in enumerate(rects, 1):
        p = out_dir / f"{prefix}-{i:02d}.png"
        safe_screenshot(page, path=str(p), full_page=True,
                        clip={"x": r["x"], "y": r["y"], "width": r["w"], "height": r["h"]})
        paths.append(str(p))
    return paths


def stitch_long(pngs: list[str], out: Path, gap: int = 0, bg: str = "#FFFFFF") -> str:
    from PIL import Image
    ims = [Image.open(p).convert("RGB") for p in pngs]
    w = max(i.width for i in ims)
    h = sum(i.height for i in ims) + gap * (len(ims) - 1)
    canvas = Image.new("RGB", (w, h), bg)
    y = 0
    for im in ims:
        canvas.paste(im, ((w - im.width) // 2, y))
        y += im.height + gap
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".jpg", ".jpeg"):
        canvas.save(out, quality=88)
    else:
        canvas.save(out)
    return str(out)


def write_pdf(page, kind: str, out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    page.add_style_tag(content=PRINT_FIX)
    page.emulate_media(media="print")
    if kind == "slides":
        page.add_style_tag(content="@page { size: 1280px 720px; margin: 0; }")
        page.pdf(path=str(out), width="1280px", height="720px", print_background=True,
                 margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
    elif kind == "doc":
        page.add_style_tag(content="@page { size: A4; margin: 0; }")
        page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                 margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
    else:                                         # 流式: 交给 @page (没有就 A4 + 18mm 边距)
        page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                 margin={"top": "18mm", "right": "16mm", "bottom": "18mm", "left": "16mm"})
    page.emulate_media(media="screen")
    return str(out)


def render(html: str, pdf: str | None = None, png: str | None = None, long: str | None = None,
           kind: str = "auto", scale: float = 1.0) -> dict:
    k = _ds.sniff_kind(html) if kind == "auto" else kind
    w, h = _ds.page_size_for(k)
    res: dict = {"html": str(Path(html).resolve())}
    with _ds.open_page(html, width=w, height=h, scale=scale) as (page, _b):
        real = _ds.detect_kind(page) if kind == "auto" else kind
        res["kind"] = real
        res["pages"] = len(page_rects(page)) or 1
        shots: list[str] = []
        if png or long:
            shots = shoot_pages(page, Path(png) if png else Path(long).parent / (Path(long).stem + "_pages"))
            if png:
                res["png"] = shots
        if long:
            res["long"] = stitch_long(shots, Path(long), gap=16 if real == "slides" else 12,
                                      bg="#E9E8E3")
            if not png:                          # 长图专用的临时分页图删掉
                for s in shots:
                    Path(s).unlink(missing_ok=True)
                try:
                    Path(shots[0]).parent.rmdir()
                except OSError:
                    pass
        if pdf:
            res["pdf"] = write_pdf(page, real, Path(pdf))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="doc-studio: HTML → PDF / PNG / 长图")
    ap.add_argument("html")
    ap.add_argument("--pdf", help="输出 PDF 路径")
    ap.add_argument("--png", help="每页截图输出目录")
    ap.add_argument("--long", help="长图输出路径 (.png/.jpg)")
    ap.add_argument("--kind", default="auto", choices=["auto", "slides", "doc", "flow"])
    ap.add_argument("--scale", type=float, default=1.0, help="截图倍率 (2 = 高清)")
    a = ap.parse_args(argv)
    if not (a.pdf or a.png or a.long):
        ap.error("至少给一个 --pdf / --png / --long")
    print(_ds.dump_json(render(a.html, a.pdf, a.png, a.long, a.kind, a.scale)))


if __name__ == "__main__":
    main()
