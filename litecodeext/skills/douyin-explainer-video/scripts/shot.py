#!/usr/bin/env python3
"""用法: python3 shot.py cover.html cover.png 1080 1440 [--json-js "<返回布局信息的 JS 表达式>"]
   封面 / 海报截图; --json-js 可顺手打印元素坐标, 查重叠/溢出。"""
import sys, glob, os, json
from playwright.sync_api import sync_playwright
src, out = sys.argv[1], sys.argv[2]
w, h = int(sys.argv[3]), int(sys.argv[4])
js = sys.argv[6] if len(sys.argv) > 6 and sys.argv[5] == "--json-js" else None
_c = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
exe = _c[-1] if _c else None
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": w, "height": h})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("file://" + os.path.abspath(src))
    pg.wait_for_timeout(200)
    if js: print("JSON:", json.dumps(pg.evaluate(js), ensure_ascii=False))
    pg.screenshot(path=out)
    if errs: print("PAGE ERRORS:", errs)
    b.close()
print("shot ->", out)
