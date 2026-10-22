#!/usr/bin/env python3
"""images.py — 配图管线: 收集 HTML 里的配图需求 → 按优先级取图 → 裁剪 → 改写 src → 写图片来源。

页面里这样占位 (img 或任意块元素; 块元素会被设成 background-image):
  <img data-need="西湖断桥残雪, 冬日湖面" data-kind="photo" data-query="West Lake Hangzhou Broken Bridge">
  <img data-need="游客量趋势" data-kind="chart" data-chart='{"type":"bar","labels":[...],"series":[{"name":"游客","data":[...]}],"unit":"万人次"}'>
  <img data-need="水墨风西湖插画" data-kind="illustration" data-query="West Lake ink painting">
  <img data-need="交通便利" data-kind="icon" data-icon="map-pin">     (→ 替换成内联 SVG, 颜色跟 currentColor)
可选属性: data-query (搜索词, 英文效果最好) · data-ratio="16:9" · data-asset="文件名" (指定用户素材)
署名位置: <span data-credit></span> 放在图片所在 figure/.photo 里 → 填短署名;
          <div data-credit-page></div> 放在页内 → 填本页所有网图署名; <div data-credits></div> → 全部图片来源清单。

取图优先级: ① --assets 目录 (用户提供/会话附件/知识库图片, 按 data-asset 或文件名关键词匹配)
            ② photo: 开放授权图库 (--sources, 默认 openverse,wikimedia; 只收 CC0/公有领域/CC BY/CC BY-SA)
            ③ illustration: comfyui-image 技能 (--comfy host:port 或 last); 没有就降级为照片搜索, 再不行纯色块
            ④ chart: matplotlib 按 data-chart 渲染透明 PNG      ⑤ icon: 内联 SVG
用法:
  python3 images.py deck.html                                  # 原地改写, 图存 deck.html 同目录 img/
  python3 images.py deck.html -o deck.filled.html --assets ~/素材 --verify
  python3 images.py deck.html --comfy 10.0.0.5:8488 --sources wikimedia --force
输出: <html 同目录>/images.json (每个需求的结果与授权), stdout 打印汇总 JSON。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ds  # noqa: E402
import _img_make as mk  # noqa: E402
import _img_sources as srcs  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_RATIO = {"photo": 3 / 2, "illustration": 3 / 2, "chart": 16 / 9}


# ── 需求收集 ───────────────────────────────────────
def parse_ratio(s: str | None) -> float | None:
    if not s:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*[:/x×]\s*(\d+(?:\.\d+)?)\s*$", s)
    if m and float(m.group(2)):
        return float(m.group(1)) / float(m.group(2))
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def collect_needs(soup, force: bool = False) -> list[dict]:
    needs = []
    for i, el in enumerate(soup.select("[data-need]")):
        kind = (el.get("data-kind") or "photo").strip().lower()
        filled = bool(el.get("data-filled")) or (el.name == "img" and (el.get("src") or "").strip()
                                                 and not el.get("src", "").startswith("data:"))
        needs.append({
            "idx": i, "id": el.get("id") or f"need-{i + 1:02d}", "tag": el.name, "kind": kind,
            "desc": (el.get("data-need") or "").strip(), "query": (el.get("data-query") or "").strip(),
            "ratio": parse_ratio(el.get("data-ratio")), "asset": (el.get("data-asset") or "").strip(),
            "chart": el.get("data-chart"), "icon": (el.get("data-icon") or "").strip(),
            "skip": bool(filled and not force),
        })
    return needs


def measure(html_path: Path, needs: list[dict]) -> None:
    """用浏览器量每个占位的版面尺寸 (决定裁剪比例与图表尺寸)。浏览器不可用时静默跳过。"""
    try:
        w, h = _ds.page_size_for(_ds.sniff_kind(html_path))
        with _ds.open_page(html_path, width=w, height=h) as (page, _b):
            rects = page.evaluate("""() => Array.from(document.querySelectorAll('[data-need]')).map(e => {
                const r = e.getBoundingClientRect(); return [r.width, r.height]; })""")
    except Exception as e:
        _ds.eprint(f"[images] 量尺寸失败 (按 data-ratio/默认比例): {e}")
        return
    for n in needs:
        if n["idx"] < len(rects):
            w, h = rects[n["idx"]]
            if w >= 8 and h >= 8:
                n["box"] = [round(w), round(h)]
                n["ratio"] = n["ratio"] or w / h
            elif n["kind"] != "icon":
                n["warn"] = f"占位元素尺寸 {round(w)}×{round(h)}px, 图会显示不出来: 给容器设高度 (height / flex:1)"
                _ds.eprint(f"[images] {n['id']} {n['warn']}")


# ── 用户素材 ───────────────────────────────────────
def tokens(text: str) -> set[str]:
    t = (text or "").lower()
    out = {w for w in re.findall(r"[a-z]{3,}", t)}
    for run in re.findall(r"[一-鿿]+", t):
        out |= {run[i:i + 2] for i in range(len(run) - 1)} or {run}
    return out


def list_assets(dirs: list[str]) -> list[Path]:
    files = []
    for d in dirs or []:
        p = Path(d).expanduser()
        if p.is_file() and p.suffix.lower() in IMG_EXT:
            files.append(p)
        elif p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in IMG_EXT)
    return files


def pick_asset(need: dict, assets: list[Path], used: set) -> Path | None:
    if need["asset"]:
        for f in assets:
            if f.name == need["asset"] or f.stem == need["asset"]:
                return f
    want = tokens(need["desc"] + " " + need["query"])
    best, best_s = None, 0
    for f in assets:
        if str(f) in used:
            continue
        s = len(want & tokens(f.stem.replace("_", " ").replace("-", " ")))
        if s > best_s:
            best, best_s = f, s
    return best


# ── 主流程 ─────────────────────────────────────────
class Ctx:
    def __init__(self, a, out_dir: Path, tokens_css: dict):
        self.a = a
        self.out_dir = out_dir
        self.assets = list_assets(a.assets)
        self.used_assets: set = set()
        self.used_urls: set = set()
        self.cache: dict = {}
        self.source_status: dict = {}
        self.palette = None                     # 图表配色跟页面 tokens 走
        if tokens_css.get("accent"):
            self.palette = [tokens_css["accent"], tokens_css.get("primary", "#1F2A44"),
                            tokens_css.get("accent-2", "#16A34A")] + mk.DEFAULT_PALETTE[3:]
        self.accent = tokens_css.get("accent", "#4F7EF7")
        self.text = tokens_css.get("text", "#1A1A1A")

    def search(self, source: str, query: str) -> list[dict]:
        key = (source, query)
        st0 = self.source_status.get(source) or {}
        if st0.get("disabled"):                     # 被拦 (403) 过的源本次不再请求
            return []
        if key not in self.cache:
            t0 = time.time()
            try:
                if source == "searxng":
                    self.cache[key] = srcs.search_searxng(query, self.a.searxng)
                else:
                    self.cache[key] = srcs.SEARCHERS[source](query)
                st = self.source_status.setdefault(source, {"ok": 0, "fail": 0, "secs": 0.0})
                st["ok"] += 1
            except Exception as e:
                self.cache[key] = []
                st = self.source_status.setdefault(source, {"ok": 0, "fail": 0, "secs": 0.0})
                st["fail"] += 1
                st["last_error"] = f"{type(e).__name__}: {e}"[:160]
                if "403" in str(e):
                    st["disabled"] = True
            st["secs"] = round(st["secs"] + time.time() - t0, 2)
        return self.cache[key]


def verify_match(img: Path, desc: str) -> tuple[bool, str]:
    prompt = (f"配图需求: 「{desc}」。判断这张图片是否基本符合需求 (主体/场景/地点对得上即可, 不苛求细节)。"
              '只输出 JSON: {"match": true 或 false, "reason": "不超过20字"}')
    ans = _ds.vision_ask([str(img)], prompt, max_tokens=400)
    if ans.startswith("ERROR"):
        return True, f"未校验 ({ans[:60]})"
    j = _ds.parse_json_loose(ans)
    if isinstance(j, dict) and "match" in j:
        return bool(j["match"]), str(j.get("reason", ""))[:60]
    return ("否" not in ans[:6] and "false" not in ans.lower()[:20]), ans[:60]


def relaxed_queries(query: str) -> list[str]:
    """图库全文检索近似"所有词都要命中": 候选太少时从尾部逐个去词放宽, 至少留 2 个词。"""
    words = query.split()
    out = [query]
    for k in range(len(words) - 1, 1, -1):
        out.append(" ".join(words[:k]))
    return out


def fetch_photo(n: dict, ctx: Ctx, dest: Path) -> dict | None:
    a = ctx.a
    query = n["query"] or n["desc"]
    sources = [s.strip() for s in a.sources.split(",") if s.strip()]
    if a.allow_unlicensed and a.searxng:
        sources.append("searxng")
    tries = a.max_tries or (6 if a.verify else 3)
    for source in sources:
        cands, seen = [], set()
        for q in relaxed_queries(query):
            for c in ctx.search(source, q):
                if (c.get("url") and c["url"] not in ctx.used_urls and c["url"] not in seen
                        and (source == "searxng" or srcs.license_ok(c["license"], a.allow_nc, a.allow_nd))
                        and (n["kind"] != "photo" or srcs.plausible_photo(c, a.min_width))):
                    seen.add(c["url"])
                    cands.append(c)
            if len(cands) >= tries:
                break
        for c in srcs.rank(cands, n["ratio"])[:tries]:
            raw = ctx.out_dir / "_raw" / f"{n['id']}{Path(c['url'].split('?')[0]).suffix or '.jpg'}"
            raw.parent.mkdir(parents=True, exist_ok=True)
            try:
                srcs.download(c["url"], raw)
                info = mk.fit_crop(raw, dest, n["ratio"])
            except Exception as e:
                _ds.eprint(f"[images] {n['id']} 下载/裁剪失败 {c['url'][:80]}: {e}")
                continue
            ok, why = (True, "") if not a.verify else verify_match(dest, n["desc"])
            if not ok:
                _ds.eprint(f"[images] {n['id']} 视觉校验不符, 换下一张: {why}")
                continue
            ctx.used_urls.add(c["url"])
            return {**{k: c.get(k, "") for k in ("source", "title", "author", "license", "license_raw",
                                                  "page_url", "license_url")},
                    "license_label": srcs.license_label(c["license"], c.get("license_raw", "")),
                    "query": query, "verify": why if a.verify else None, **info}
    return None


def fill_one(n: dict, ctx: Ctx) -> dict:
    a, kind = ctx.a, n["kind"]
    box = n.get("box") or [960, int(960 / (n["ratio"] or DEFAULT_RATIO.get(kind, 1.5)))]
    n["ratio"] = n["ratio"] or DEFAULT_RATIO.get(kind, 1.5)
    rel = Path(a.img_dir) / f"{n['id']}.jpg"
    dest = ctx.out_dir / rel
    if kind == "icon":
        return {"status": "ok", "via": "icon", "icon": n["icon"] or mk.guess_icon(n["desc"])}
    if kind == "chart":
        spec = mk.parse_chart_spec(n["chart"], ctx.out_dir)
        if not spec:
            return {"status": "failed", "via": "chart", "error": "data-chart 缺失或不是合法 JSON"}
        rel = rel.with_suffix(".png")
        mk.make_chart(spec, ctx.out_dir / rel, box[0], box[1], ctx.palette, ctx.text)
        return {"status": "ok", "via": "chart", "file": rel.as_posix(), "source": "matplotlib"}
    asset = pick_asset(n, ctx.assets, ctx.used_assets) if ctx.assets else None
    if asset:
        ctx.used_assets.add(str(asset))
        info = mk.fit_crop(asset, dest, n["ratio"])
        return {"status": "ok", "via": "asset", "file": rel.as_posix(), "source": "用户素材",
                "asset": str(asset), **info}
    if kind == "illustration" and a.comfy:
        prompt = n["query"] or n["desc"]
        try:
            raw = srcs.run_comfy(a.comfy, prompt, n["ratio"], ctx.out_dir / "_raw" / f"{n['id']}_comfy.png")
            info = mk.fit_crop(raw, dest, n["ratio"])
            return {"status": "ok", "via": "comfyui", "file": rel.as_posix(), "source": "AI 生成 (ComfyUI)",
                    "prompt": prompt, **info}
        except Exception as e:
            _ds.eprint(f"[images] {n['id']} ComfyUI 生图失败, 降级找照片: {e}")
    if kind in ("photo", "illustration"):
        rec = fetch_photo(n, ctx, dest)
        if rec:
            return {"status": "ok", "via": "search", "file": rel.as_posix(), **rec}
    rel = rel.with_suffix(".png")
    mk.placeholder(ctx.out_dir / rel, box[0], box[1], ctx.accent)
    return {"status": "fallback", "via": "placeholder", "file": rel.as_posix(),
            "error": "没有找到合适的授权图片" if kind == "photo" else "无生图服务, 已用色块占位"}


def short_credit(r: dict) -> str:
    who = (r.get("author") or "").strip() or "佚名"
    site = {"wikimedia": "Wikimedia Commons", "openverse": "Openverse"}.get(r.get("source"), r.get("source", ""))
    return f"图: {who} · {r.get('license_label', '')} · {site}"


def apply(soup, needs: list[dict], results: dict) -> list[dict]:
    """results 含本次新取的 + 上次保留的 (重跑时署名整体重建, 换掉的图不会留下旧作者)。"""
    els = soup.select("[data-need]")
    credits = []
    for slot in soup.select("[data-credit-page], [data-credit][data-credit-auto]"):
        slot.clear()
        if slot.has_attr("data-credit-items"):
            del slot["data-credit-items"]
    for n in needs:
        r = results.get(n["id"])
        if not r or n["idx"] >= len(els):
            continue
        el = els[n["idx"]]
        if r["via"] == "icon":
            if n["skip"]:
                continue
            from bs4 import BeautifulSoup
            cls = " ".join(["ds-icon"] + [c for c in (el.get("class") or []) if c != "ds-icon"])
            svg = BeautifulSoup(mk.icon_svg(r["icon"], cls=cls), "html.parser").svg
            if el.get("style"):
                svg["style"] = el["style"]
            svg["data-need"], svg["data-kind"], svg["data-filled"] = n["desc"], "icon", "icon"
            el.replace_with(svg)
            continue
        if not r.get("file"):
            continue
        if el.name == "img":
            el["src"] = r["file"]
            el["alt"] = el.get("alt") or n["desc"]
        else:
            style = re.sub(r"background-image\s*:[^;]+;?", "", el.get("style") or "").strip()
            el["style"] = (style + ("; " if style and not style.endswith(";") else " ") +
                           f"background-image:url('{r['file']}'); background-size:cover; "
                           "background-position:center").strip()
        el["data-filled"] = r["via"]
        if r.get("via") == "search":
            sc = short_credit(r)
            el["data-credit-text"] = sc
            credits.append({**r, "id": n["id"], "desc": n["desc"], "short": sc, "_el": el})
    # 图旁短署名 / 页内署名 / 全文来源清单
    for c in credits:
        el = c["_el"]
        box = el.find_parent(["figure"]) or el.find_parent(class_="photo") or el.parent
        slot = box.select_one("[data-credit]") if box else None
        if slot is not None and not slot.get_text(strip=True):
            slot.string = c["short"]
            slot["data-credit-auto"] = "1"
        sec = el.find_parent("section")
        page_slot = sec.select_one("[data-credit-page]") if sec else None
        if page_slot is not None:                       # 页脚一行写完: "图片: 甲 (CC BY 4.0); 乙 (CC0) · Wikimedia Commons"
            items = page_slot.get("data-credit-items", "")
            item = f"{(c.get('author') or '佚名').strip()[:28]} ({c.get('license_label', '')})"
            if item not in items.split("; "):
                items = f"{items}; {item}" if items else item
            page_slot["data-credit-items"] = items
            sites = {"wikimedia": "Wikimedia Commons", "openverse": "Openverse"}
            page_slot.string = f"图片: {items} · {sites.get(c.get('source'), c.get('source', ''))}"
    lst = soup.select_one("[data-credits]")
    if lst is not None and credits:
        lst.clear()
        title = soup.new_tag("div")
        title["class"] = "credits-title"
        title.string = "图片来源"
        ol = soup.new_tag("ol")
        for c in credits:
            li = soup.new_tag("li")
            li.string = (f"{c['desc']}: 《{c.get('title') or '无题'}》 — {c.get('author') or '佚名'}, "
                         f"{c.get('license_label')}, {urllib.parse.unquote(c.get('page_url') or '')}")
            ol.append(li)
        lst.append(title)
        lst.append(ol)
    for c in credits:
        c.pop("_el", None)
    return credits


def run(html: str, out: str | None = None, argv_ns=None) -> dict:
    from bs4 import BeautifulSoup
    a = argv_ns
    src = Path(html).resolve()
    dst = Path(out).resolve() if out else src
    out_dir = dst.parent
    txt = src.read_text(encoding="utf-8")
    soup = BeautifulSoup(txt, "html.parser")
    needs = collect_needs(soup, a.force)
    only = {x.strip() for x in (getattr(a, "only", "") or "").split(",") if x.strip()}
    if only:                                           # 只重取指定的几张 (修图轮次用), 隐含 --force
        for n in needs:
            n["skip"] = n["id"] not in only
    todo = [n for n in needs if not n["skip"]]
    if todo and a.measure:
        measure(src, todo)
    ctx = Ctx(a, out_dir, mk.css_tokens(txt))
    results, t0 = {}, time.time()
    for n in todo:
        t1 = time.time()
        try:
            results[n["id"]] = fill_one(n, ctx)
        except Exception as e:
            results[n["id"]] = {"status": "failed", "via": n["kind"], "error": f"{type(e).__name__}: {e}"}
        results[n["id"]]["secs"] = round(time.time() - t1, 2)
        _ds.eprint(f"[images] {n['id']} {n['kind']:<12} {results[n['id']]['status']:<8} "
                   f"{results[n['id']].get('via')} {results[n['id']].get('file', '')}")
    mpath = Path(a.manifest) if a.manifest else out_dir / "images.json"
    prev = {}
    try:                                               # 上次的结果: 保留的图继续署名
        prev = {n["id"]: n for n in json.loads(mpath.read_text(encoding="utf-8")).get("needs", [])
                if n.get("status") in ("ok", "fallback") and n.get("via") != "icon"}
    except (OSError, ValueError):
        pass
    merged = {**{n["id"]: prev[n["id"]] for n in needs if n["skip"] and n["id"] in prev}, **results}
    credits = apply(soup, needs, merged)
    dst.write_text(str(soup), encoding="utf-8")
    _ds.ensure_base_css(dst)
    keep = ("status", "via", "file", "source", "title", "author", "license", "license_raw", "license_label",
            "page_url", "license_url", "query", "verify", "asset", "prompt", "error", "secs",
            "width", "height", "src_width", "src_height", "icon")
    manifest = {"html": str(dst), "needs": [{**{k: v for k, v in n.items() if k != "chart"},
                                             **{k: v for k, v in merged.get(n["id"], {"status": "kept"}).items()
                                                if k in keep}} for n in needs],
                "credits": credits, "sources": ctx.source_status, "secs": round(time.time() - t0, 1)}
    _ds.dump_json(manifest, mpath)
    stat = [r["status"] for r in results.values()]
    return {"html": str(dst), "manifest": str(mpath), "total": len(needs), "filled": stat.count("ok"),
            "fallback": stat.count("fallback"), "failed": stat.count("failed"), "kept": len(needs) - len(todo),
            "credits": len(credits), "sources": ctx.source_status}


def build_parser():
    ap = argparse.ArgumentParser(description="doc-studio 配图管线")
    ap.add_argument("html")
    ap.add_argument("-o", "--out", help="输出 HTML (默认原地改写)")
    ap.add_argument("--assets", action="append", default=[], help="用户素材目录/文件 (可多次)")
    ap.add_argument("--sources", default="openverse,wikimedia", help="照片源顺序, 逗号分隔")
    ap.add_argument("--comfy", default="", help="ComfyUI 地址 host:port, 或 last=用上次记住的")
    ap.add_argument("--verify", action="store_true", help="视觉模型判断图文是否相符, 不符换下一张")
    ap.add_argument("--max-tries", type=int, default=0, help="每个需求最多试几张候选 (默认 3, 开 --verify 时 6)")
    ap.add_argument("--min-width", type=int, default=800, help="照片最小原始宽度")
    ap.add_argument("--allow-nc", action="store_true", help="允许非商用 (NC) 授权")
    ap.add_argument("--allow-nd", action="store_true", help="允许禁止演绎 (ND) 授权 (注意: 裁剪算演绎)")
    ap.add_argument("--allow-unlicensed", action="store_true", help="开放源都没有时用 SearXNG 网图 (授权未知)")
    ap.add_argument("--searxng", default="http://127.0.0.1:18888")
    ap.add_argument("--img-dir", default="img", help="图片存放子目录 (相对输出 HTML)")
    ap.add_argument("--manifest", help="清单 JSON 路径 (默认 HTML 同目录 images.json)")
    ap.add_argument("--force", action="store_true", help="已填过的占位也重新取图")
    ap.add_argument("--only", default="", help="只重取这些需求 id (逗号分隔, 见 images.json; 元素有 id 属性时就是它)")
    ap.add_argument("--no-measure", dest="measure", action="store_false", help="不开浏览器量尺寸")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    print(_ds.dump_json(run(a.html, a.out, a)))


if __name__ == "__main__":
    main()
