#!/usr/bin/env python3
"""用法: python3 shot.py cover.html cover.png 1080 1440 [--json-js "<返回布局信息的 JS 表达式>"]
   封面 / 海报截图 + 版面自检 (出屏/重叠/撑破框, 有问题非 0 退出); --json-js 可顺手打印元素坐标。"""
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
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from render import _CHECK_JS, _issues          # 跟视频同一套版面自检: 出屏 / 重叠 / 撑破框
    bad = sorted(set(_issues(pg.evaluate(_CHECK_JS), w, h)))
    for m in bad: print("  ✗", m)
    print("版面自检:", "通过" if not bad else f"{len(bad)} 处问题")
    pg.screenshot(path=out)
    if errs: print("PAGE ERRORS:", errs)
    b.close()
print("shot ->", out)
sys.exit(1 if bad else 0)
