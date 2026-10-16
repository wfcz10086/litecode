#!/usr/bin/env python3
"""step2/3: timeline.json → tl.js → playwright 驱动 video.html 的 seek(t)
  python3 render.py check              版面自检: 每场 4 个时刻量所有可见文字的实际框, 查文字互相压住 / 出屏
  python3 render.py snap [t1 t2 ...]   关键帧预览 → preview/t<t>.png (默认每场中点)
  python3 render.py all                逐帧 → frames/f00000.jpg ... (耗时长, 用 background 跑)
                                       多进程并行 (RENDER_WORKERS, 默认 CPU 核数/2, 最多 6) + CDP 直出 JPEG;
                                       FRAME_FMT=png 退回无损 PNG
有 page error 或 check 发现问题 → 非 0 退出。
故意叠在别的字上的元素 (印章盖在行情牌上之类) 加 data-qa="overlay" 豁免。"""
import base64, glob, json, os, re, sys, time
from multiprocessing import Pool

from playwright.sync_api import sync_playwright

FPS = int(os.environ.get("FPS", 24))
FMT = os.environ.get("FRAME_FMT", "jpg")          # jpg: 编码快、体积约 PNG 的 1/5; 成片是 h264 有损, 看不出差别
WORKERS = int(os.environ.get("RENDER_WORKERS", max(1, min(6, (os.cpu_count() or 2) // 2))))


def browser(pw):
    exe = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    return pw.chromium.launch(executable_path=exe[-1] if exe else None, args=["--no-sandbox", "--force-color-profile=srgb"])


# 页面里跑: 收集可见文字框 (有效不透明度 = 自身×所有祖先, 透明字/描边字跳过)
_CHECK_JS = r"""() => {
  const out = [], els = [];
  const vis = el => { let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === "none" || cs.visibility === "hidden") return 0;
      o *= parseFloat(cs.opacity); }
    return o; };
  const alpha = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return 1; const p = m[1].split(",");
    return p.length > 3 ? parseFloat(p[3]) : 1; };
  for (const el of document.body.querySelectorAll("*")) {
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join("").trim();
    if (!own) continue;
    const cs = getComputedStyle(el);
    if (alpha(cs.color) < .3) continue;
    const o = vis(el); if (o < .35) continue;
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) continue;
    let overlay = false; for (let e = el; e; e = e.parentElement) if (e.dataset && e.dataset.qa === "overlay") overlay = true;
    els.push(el);
    out.push({id: el.id || "", cls: (el.className && el.className.baseVal === undefined ? el.className : "") || "",
              spill: el.scrollWidth > el.clientWidth + 4 && el.clientWidth > 0 && getComputedStyle(el).overflow === "visible",
              text: own.slice(0, 16), x: r.left, y: r.top, r: r.right, b: r.bottom, overlay, sub: !!el.closest("#sub")});
  }
  // 互为祖先/后代的文字 (标题里套高亮 span) 不算重叠
  out.forEach((b, i) => { b.rel = els.map((e, j) => j !== i && (e.contains(els[i]) || els[i].contains(e)) ? j : -1).filter(j => j >= 0); });
  return out; }"""


def _issues(boxes, w=1080, h=1920):
    found = []
    for b in boxes:
        if b["overlay"]:
            continue
        if b.get("spill") and not b["sub"]:
            found.append(f"撑破框: 「{b['text']}」 文字比自己的框宽 (加宽 / 缩字号 / 允许换行)")
        if b["x"] < -4 or b["r"] > w + 4 or b["y"] < -4 or b["b"] > h + 4:
            found.append(f"出屏: 「{b['text']}」 x={b['x']:.0f}..{b['r']:.0f} y={b['y']:.0f}..{b['b']:.0f}")
    for i, a in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            c = boxes[j]
            if a["overlay"] or c["overlay"] or j in a.get("rel", ()):
                continue
            iw = min(a["r"], c["r"]) - max(a["x"], c["x"]); ih = min(a["b"], c["b"]) - max(a["y"], c["y"])
            if iw <= 0 or ih <= 0:
                continue
            # 细缝 (行距紧的两行字外框上下沾边) 不算: 字形其实没碰到
            if ih < .25 * min(a["b"] - a["y"], c["b"] - c["y"]) or iw < .25 * min(a["r"] - a["x"], c["r"] - c["x"]):
                continue
            small = min((a["r"] - a["x"]) * (a["b"] - a["y"]), (c["r"] - c["x"]) * (c["b"] - c["y"]))
            if iw * ih > .12 * small:
                found.append(f"重叠: 「{a['text']}」 × 「{c['text']}」 ({iw:.0f}x{ih:.0f}px)")
    return found


def check(pg, tl):
    bad = 0
    for i, s in enumerate(tl["scenes"]):
        subs = []
        for f in (.25, .5, .75, .95):
            t = round(s["start"] + s["dur"] * f, 2)
            pg.evaluate(f"seek({t})")
            subs.append(pg.evaluate("() => (document.getElementById('sub') || {}).innerText || ''"))
            for msg in sorted(set(_issues(pg.evaluate(_CHECK_JS)))):
                bad += 1
                print(f"  ✗ {s['id']} t={t}s {msg}")
        # 字幕要跟着旁白往下走: 旁白 ≥3 句而 4 个时刻字幕都一样 = 卡在某一句 (原片就是每场卡第一句)
        phrases = [x for x in re.split(r"(?<=[，。！？；：])", s.get("text", "")) if x.strip()]
        if len(phrases) >= 3 and len({x.strip() for x in subs}) <= 1:
            bad += 1
            print(f"  ✗ {s['id']} 字幕卡住: 4 个时刻都是「{subs[0][:16]}」, 旁白有 {len(phrases)} 句")
    print("版面自检:", "通过" if not bad else f"{bad} 处问题 (修 video.html/engine.js; 故意叠放的加 data-qa=\"overlay\")")
    return bad


def _render_range(job):
    """一个 worker: 自己的浏览器渲 [a, b) 帧。CDP captureScreenshot 比 page.screenshot 少一层封装, 再开速度优先。"""
    k, a, b = job
    with sync_playwright() as pw:
        br = browser(pw)
        pg = br.new_page(viewport={"width": 1080, "height": 1920})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("file://" + os.path.abspath("video.html"))
        pg.wait_for_timeout(400)
        cdp = pg.context.new_cdp_session(pg)
        shot = ({"format": "jpeg", "quality": 92, "optimizeForSpeed": True} if FMT == "jpg"
                else {"format": "png", "optimizeForSpeed": True})
        t0 = time.time()
        for i in range(a, b):
            pg.evaluate(f"seek({i / FPS:.4f})")
            data = cdp.send("Page.captureScreenshot", shot)["data"]
            with open(f"frames/f{i:05d}.{FMT}", "wb") as f:
                f.write(base64.b64decode(data))
            if (i - a) % 100 == 0:
                print(f"[w{k}] {i - a}/{b - a} {time.time() - t0:.0f}s", flush=True)
        br.close()
        return errs


def render_all(tl):
    os.makedirs("frames", exist_ok=True)
    for old in glob.glob("frames/f*.*"):               # 换格式时别混着上一轮的帧
        os.remove(old)
    n = int(tl["total"] * FPS) + 1
    w = max(1, min(WORKERS, n // 50 or 1))
    jobs = [(k, k * n // w, (k + 1) * n // w) for k in range(w)]
    t0 = time.time()
    print(f"{n} frames, {w} workers, {FMT}", flush=True)
    with Pool(w) as pool:
        errs = [e for r in pool.map(_render_range, jobs) for e in r]
    got = len(glob.glob(f"frames/f*.{FMT}"))
    print(f"DONE {got}/{n} frames {time.time() - t0:.0f}s ({got / max(time.time() - t0, 1):.1f} fps)")
    if errs:
        print("PAGE ERRORS DURING SEEK:", errs[:5])
    return got == n and not errs


def check_page(pw, tl):
    b = browser(pw)
    pg = b.new_page(viewport={"width": 1080, "height": 1920})
    pg.goto("file://" + os.path.abspath("video.html")); pg.wait_for_timeout(400)
    n = check(pg, tl); b.close(); return n


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
        if mode == "check":
            b.close()
            sys.exit(1 if check_page(pw, tl) else 0)
        if mode == "all":
            b.close()
            sys.exit(0 if render_all(tl) else 1)
        if mode == "snap":
            os.makedirs("preview", exist_ok=True)
            ts = [float(x) for x in sys.argv[2:]] or [round(s["start"] + s["dur"] / 2, 1) for s in tl["scenes"]]
            for t in ts:
                pg.evaluate(f"seek({t})"); pg.screenshot(path=f"preview/t{t:g}.png"); print("snap", f"preview/t{t:g}.png")
        else:
            print(f"未知模式 {mode}, 可选: check / snap / all"); b.close(); sys.exit(2)
        b.close()
        if errs:
            print("PAGE ERRORS DURING SEEK:", errs); sys.exit(1)


if __name__ == "__main__":
    main()
