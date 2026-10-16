#!/usr/bin/env python3
"""step2/3: timeline.json → tl.js → playwright 驱动 video.html 的 seek(t)
  python3 render.py snap [t1 t2 ...]   关键帧预览 → preview/t<t>.png (默认每场中点)
  python3 render.py all                逐帧 → frames/f00000.png ... (耗时长, 用 background 跑)
有 page error 直接非 0 退出。"""
import glob, json, os, sys, time

from playwright.sync_api import sync_playwright

FPS = int(os.environ.get("FPS", 24))


def browser(pw):
    exe = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    return pw.chromium.launch(executable_path=exe[-1] if exe else None, args=["--no-sandbox", "--force-color-profile=srgb"])


def main():
    tl = json.load(open("timeline.json", encoding="utf-8"))
    open("tl.js", "w", encoding="utf-8").write("window.TL=" + json.dumps(tl, ensure_ascii=False) + ";")
    mode = sys.argv[1] if len(sys.argv) > 1 else "snap"
    with sync_playwright() as pw:
        b = browser(pw)
        pg = b.new_page(viewport={"width": 1080, "height": 1920})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("file://" + os.path.abspath("video.html"))
        pg.wait_for_timeout(400)
        if errs:
            print("PAGE ERRORS:", errs); b.close(); sys.exit(1)
        if mode == "snap":
            os.makedirs("preview", exist_ok=True)
            ts = [float(x) for x in sys.argv[2:]] or [round(s["start"] + s["dur"] / 2, 1) for s in tl["scenes"]]
            for t in ts:
                pg.evaluate(f"seek({t})"); pg.screenshot(path=f"preview/t{t:g}.png"); print("snap", f"preview/t{t:g}.png")
        else:
            os.makedirs("frames", exist_ok=True)
            n, t0 = int(tl["total"] * FPS) + 1, time.time()
            for i in range(n):
                pg.evaluate(f"seek({i / FPS:.4f})")
                pg.screenshot(path=f"frames/f{i:05d}.png")
                if i % 100 == 0:
                    print(f"{i}/{n} {time.time() - t0:.0f}s", flush=True)
            print(f"DONE {n} frames {time.time() - t0:.0f}s")
        b.close()
        if errs:
            print("PAGE ERRORS DURING SEEK:", errs); sys.exit(1)


if __name__ == "__main__":
    main()
