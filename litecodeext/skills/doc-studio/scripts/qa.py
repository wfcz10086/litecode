#!/usr/bin/env python3
"""qa.py — 成品质检: 每页截图 + 规则检查 (+ 可选视觉模型评审) → 问题清单 JSON。

规则 (浏览器里按计算后的布局量):
  overflow     文字超出页面 / 溢出所在卡片 / 被 overflow:hidden 裁掉        (error)
  overlap      两段文字互相压住                                           (error)
  blank        空白页 / 几乎没内容                                         (error / warn)
  image        图片没加载出来 / 配图占位 data-need 没填                     (error / warn)
  font         字太小 (幻灯片 < 14px, 文档 < 11px)                        (warn)
  dense        单页字数过多 (幻灯片 > 220 字)                              (warn)
  contrast     文字与纯色底对比度 < 3:1                                    (warn)
  vision       --vision 时视觉模型看截图提的问题                            (warn)
用法:
  python3 qa.py deck.html --shots qa_shots/ -o qa.json
  python3 qa.py doc.html --vision --strict          # 有 error 时退出码 2 (DAG 里可接条件分支)
修复约定 (SKILL.md): 按 issues 改 HTML 后重跑, 最多 2 轮; 第 2 轮仍有 error 就把剩余问题如实告诉用户。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ds  # noqa: E402
import render  # noqa: E402

CHECK_JS = r"""
(args) => {
  const SEL = args.selector, KIND = args.kind;
  const issues = [], overImg = [];
  const cs = e => getComputedStyle(e);
  const px = v => parseFloat(v) || 0;
  const color = s => { const m = (s || '').match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const path = e => { const parts = []; for (let n = e; n && n.nodeType === 1 && parts.length < 4 && !n.matches(SEL); n = n.parentElement) {
      let s = n.tagName.toLowerCase(); if (n.id) s += '#' + n.id; else if (n.classList.length) s += '.' + Array.from(n.classList).slice(0, 2).join('.');
      parts.unshift(s); } return parts.join(' > '); };
  const snippet = e => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
  const add = (page, type, severity, el, detail) => issues.push({page, type, severity, selector: el ? path(el) : '', text: el ? snippet(el) : '', detail});
  const ownText = e => Array.from(e.childNodes).some(n => n.nodeType === 3 && n.nodeValue.trim());
  const textRect = e => { const r = document.createRange(); r.selectNodeContents(e); return r.getBoundingClientRect(); };
  const ignored = e => !!e.closest('[data-qa=ignore], aside.notes');
  const visible = e => { const s = cs(e); return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0.05; };

  const pages = Array.from(document.querySelectorAll(SEL));
  pages.forEach((sec, i) => {
    const P = i + 1, sr = sec.getBoundingClientRect();
    const all = Array.from(sec.querySelectorAll('*')).filter(e => !ignored(e) && visible(e));
    const textEls = all.filter(ownText);
    // 空白页
    const skipTxt = Array.from(sec.querySelectorAll('[data-credits], .credits, aside.notes, [data-qa=ignore]'))
                         .reduce((n, e) => n + (e.innerText || '').replace(/\s/g, '').length, 0);
    const chars = (sec.innerText || '').replace(/\s/g, '').length - skipTxt;
    const media = all.filter(e => ['IMG', 'SVG', 'CANVAS', 'VIDEO'].includes(e.tagName.toUpperCase()) ||
                                 (cs(e).backgroundImage || 'none') !== 'none').length;
    if (!chars && !media) add(P, 'blank', 'error', sec, '空白页');
    else if (chars < 6 && !media) add(P, 'blank', 'warn', sec, `内容过少 (${chars} 字)`);
    if (KIND === 'slides' && chars > 220) add(P, 'dense', 'warn', sec, `本页 ${chars} 字, 幻灯片建议 ≤ 220 字, 拆页或精简`);
    // 图片
    sec.querySelectorAll('img').forEach(img => {
      if (ignored(img)) return;
      if (!img.getAttribute('src')) add(P, 'image', img.dataset.need ? 'warn' : 'error', img, img.dataset.need ? `配图占位未填: ${img.dataset.need}` : '图片没有 src');
      else if (img.complete && img.naturalWidth === 0) add(P, 'image', 'error', img, `图片加载失败: ${img.getAttribute('src').slice(0, 80)}`);
    });
    // 配图显示不出来 (容器没给高度) / 被页面边缘裁掉
    const clipsPage = cs(sec).overflow !== 'visible';
    sec.querySelectorAll('*').forEach(m => {
      const tag = m.tagName.toLowerCase();
      if (ignored(m) || (m.closest('svg') !== null && tag !== 'svg')) return;
      const hasPic = tag === 'img' ? !!m.getAttribute('src')
                   : (tag === 'svg' || tag === 'canvas' || /url\(/.test(cs(m).backgroundImage || ''));
      if (!hasPic || !visible(m)) return;
      const r = m.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) {
        add(P, 'image', 'error', m, `配图没显示出来 (尺寸 ${Math.round(r.width)}×${Math.round(r.height)}px, 容器没给高度?)`); return; }
      const out = Math.max(r.bottom - sr.bottom, r.right - sr.right, sr.left - r.left, sr.top - r.top);
      const bleed = Math.abs(r.width - sr.width) < 2 && Math.abs(r.height - sr.height) < 2;   // 整页背景图不算
      if (clipsPage && out > 8 && !bleed) add(P, 'image', 'error', m, `图片/图表超出页面 ${Math.round(out)}px, 被裁掉了`);
    });
    // 越界 / 溢出 / 裁切
    textEls.forEach(e => {
      const r = textRect(e);
      if (r.width < 1 || r.height < 1) return;
      if (r.bottom > sr.bottom + 2 || r.right > sr.right + 2 || r.left < sr.left - 2 || r.top < sr.top - 2)
        add(P, 'overflow', 'error', e, `文字超出页面 ${Math.round(Math.max(r.bottom - sr.bottom, r.right - sr.right, sr.left - r.left, sr.top - r.top))}px`);
      for (let a = e; a && a !== sec; a = a.parentElement) {
        const s = cs(a);
        const clipped = s.overflow !== 'visible' || s.overflowY !== 'visible';
        if (clipped && (a.scrollHeight > a.clientHeight + 2 || a.scrollWidth > a.clientWidth + 2)) {
          add(P, 'overflow', 'error', a, `内容被裁切 (需要 ${a.scrollHeight}px, 只有 ${a.clientHeight}px)`); break; }
        const boxed = a !== e && (color(s.backgroundColor) && color(s.backgroundColor).a > 0.05 || px(s.borderTopWidth) + px(s.borderBottomWidth) > 0);
        if (boxed) { const ar = a.getBoundingClientRect();
          if (r.bottom > ar.bottom + 2 || r.right > ar.right + 2) add(P, 'overflow', 'error', e, `文字溢出所在卡片 ${Math.round(Math.max(r.bottom - ar.bottom, r.right - ar.right))}px`);
          break; }
      }
      // 字号
      const meta = !!e.closest('.credit, .credits, [data-credit], [data-credits], [data-credit-page], .footer, .page-footer, figcaption, .src, .source');
      const fs = px(cs(e).fontSize);
      if (fs && fs < (KIND === 'slides' ? 14 : 11) && !meta)
        add(P, 'font', 'warn', e, `字号 ${fs}px 偏小`);
      // 对比度 (只算纯色底; 底下有图片就跳过)
      const fg = color(cs(e).color);
      if (fg && !meta) { let bg = null;
        for (let a = e; a; a = a.parentElement) { const s = cs(a);
          if ((s.backgroundImage || 'none') !== 'none') { bg = 'img'; break; }
          const c = color(s.backgroundColor); if (c && c.a > 0.9) { bg = c; break; } }
        const under = Array.from(sec.querySelectorAll('img')).some(im => { const ir = im.getBoundingClientRect();
          return !im.contains(e) && ir.left < r.right && ir.right > r.left && ir.top < r.bottom && ir.bottom > r.top; });
        if (bg && bg !== 'img' && !under) { const k = contrast(fg, bg);
          if (k < 3) add(P, 'contrast', 'warn', e, `对比度 ${k.toFixed(2)}:1 偏低`); }
        else if ((bg === 'img' || under) && fg.a > 0.5)     // 图上的字: 交给 Python 按截图像素算
          overImg.push({page: P, selector: path(e), text: snippet(e), fg: fg,
                        x: r.left - sr.left, y: r.top - sr.top, w: r.width, h: r.height}); }
    });
    // 文字互相压住
    const rects = textEls.map(e => [e, textRect(e)]).filter(([, r]) => r.width > 1 && r.height > 1);
    for (let a = 0; a < rects.length; a++) for (let b = a + 1; b < rects.length; b++) {
      const [ea, ra] = rects[a], [eb, rb] = rects[b];
      if (ea.contains(eb) || eb.contains(ea)) continue;
      const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left), h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      // 文字框含字体上下留白, 行距紧时相邻两行会"擦边": 纵向压住超过矮者 45% 才算重叠
      if (w > 2 && h > 0.45 * Math.min(ra.height, rb.height) && w * h > 0.2 * Math.min(ra.width * ra.height, rb.width * rb.height))
        add(P, 'overlap', 'error', ea, `与「${snippet(eb)}」重叠`);
    }
  });
  return {pages: pages.length, issues, overImg};
}
"""

VISION_PROMPT = """你是资深版式设计评审。这些是同一份{what}的第 {start}~{end} 页截图 (按顺序)。
逐页检查: 文字溢出/被遮挡/重叠、图片与文字内容不符、图片变形或模糊、留白失衡、对齐混乱、配色刺眼或对比度低、字太小。
只报真正影响观感的问题, 没问题的页不要写。只输出 JSON 数组:
[{{"page": 页码(整数), "problem": "问题(≤30字)", "fix": "改法(≤30字)"}}]  没有问题就输出 []"""


def run_rules(html: str, kind_arg: str, shots_dir: str | None) -> dict:
    kind = _ds.sniff_kind(html) if kind_arg == "auto" else kind_arg
    w, h = _ds.page_size_for(kind)
    with _ds.open_page(html, width=w, height=h) as (page, _b):
        kind = _ds.detect_kind(page) if kind_arg == "auto" else kind_arg
        sel = "section.slide" if kind == "slides" else ("section.page" if kind == "doc" else "body")
        res = page.evaluate(CHECK_JS, {"selector": sel, "kind": kind})
        tmp = None
        if not shots_dir:                         # 不要截图也临时截一份 (像素规则要用), 结束删掉
            import tempfile
            tmp = tempfile.mkdtemp(prefix="qa_shots_")
        shots = render.shoot_pages(page, Path(shots_dir or tmp))
    res.update(kind=kind, shots=[] if tmp else shots)
    if shots:                                     # 图上文字: 用截图里文字框区域的中位色当背景算对比度
        res["issues"] += over_image_contrast(res.get("overImg") or [], shots)
    res.pop("overImg", None)
    if shots:                                     # 像素级空白页兜底
        from PIL import Image, ImageStat
        for i, p in enumerate(shots, 1):
            st = ImageStat.Stat(Image.open(p).convert("L"))
            if st.stddev[0] < 2.0 and not any(x["page"] == i and x["type"] == "blank" for x in res["issues"]):
                res["issues"].append({"page": i, "type": "blank", "severity": "error", "selector": "",
                                      "text": "", "detail": "截图几乎是纯色 (空白页)"})
    if tmp:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def over_image_contrast(items: list[dict], shots: list[str]) -> list[dict]:
    from PIL import Image, ImageStat
    out, cache = [], {}
    for it in items:
        i = it["page"] - 1
        if i >= len(shots):
            continue
        im = cache.setdefault(i, Image.open(shots[i]).convert("RGB"))
        box = (max(0, int(it["x"])), max(0, int(it["y"])), min(im.width, int(it["x"] + it["w"])),
               min(im.height, int(it["y"] + it["h"])))
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            continue
        med = ImageStat.Stat(im.crop(box)).median
        k = _contrast(it["fg"], {"r": med[0], "g": med[1], "b": med[2]})
        if k < 2.5:
            out.append({"page": it["page"], "type": "contrast", "severity": "warn", "selector": it["selector"],
                        "text": it["text"], "detail": f"图上文字对比度约 {k:.1f}:1, 看不清; 加深色遮罩或换浅色字"})
    return out


def _contrast(a: dict, b: dict) -> float:
    def lum(c):
        f = [(v / 255) / 12.92 if v / 255 <= 0.03928 else ((v / 255 + 0.055) / 1.055) ** 2.4
             for v in (c["r"], c["g"], c["b"])]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def run_vision(shots: list[str], kind: str, batch: int = 4) -> list[dict]:
    out = []
    what = "幻灯片" if kind == "slides" else "文档"
    for s in range(0, len(shots), batch):
        group = shots[s:s + batch]
        ans = _ds.vision_ask(group, VISION_PROMPT.format(what=what, start=s + 1, end=s + len(group)), 1500)
        if ans.startswith("ERROR"):
            out.append({"page": s + 1, "type": "vision", "severity": "info", "selector": "", "text": "",
                        "detail": f"视觉评审不可用: {ans[:80]}"})
            break
        for x in _ds.parse_json_loose(ans) or []:
            if isinstance(x, dict) and x.get("problem"):
                pg = x.get("page")
                pg = pg if isinstance(pg, int) and s < pg <= s + len(group) else s + 1
                out.append({"page": pg, "type": "vision", "severity": "warn", "selector": "",
                            "text": "", "detail": f"{x['problem']} → {x.get('fix', '')}"})
    return out


def dedupe(issues: list[dict]) -> list[dict]:
    seen, out = set(), []
    for x in issues:
        k = (x["page"], x["type"], x["selector"], x["detail"][:20])
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def check(html: str, kind: str = "auto", shots: str | None = None, vision: bool = False) -> dict:
    if vision and not shots:
        shots = str(Path(html).resolve().parent / "qa_shots")
    res = run_rules(html, kind, shots)
    issues = res["issues"]
    if vision and res["shots"]:
        issues += run_vision(res["shots"], res["kind"])
    issues = dedupe(issues)
    order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda x: (x["page"], order.get(x["severity"], 3)))
    counts = {k: sum(1 for x in issues if x["severity"] == k) for k in ("error", "warn", "info")}
    return {"html": str(Path(html).resolve()), "kind": res["kind"], "pages": res["pages"],
            "counts": counts, "ok": counts["error"] == 0, "issues": issues, "shots": res["shots"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="doc-studio 质检")
    ap.add_argument("html")
    ap.add_argument("--kind", default="auto", choices=["auto", "slides", "doc", "flow"])
    ap.add_argument("--shots", help="每页截图输出目录")
    ap.add_argument("--vision", action="store_true", help="再让视觉模型看截图评审")
    ap.add_argument("-o", "--out", help="问题清单 JSON 输出路径")
    ap.add_argument("--strict", action="store_true", help="有 error 时退出码 2")
    a = ap.parse_args(argv)
    res = check(a.html, a.kind, a.shots, a.vision)
    print(_ds.dump_json(res, a.out))
    if a.strict and not res["ok"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
