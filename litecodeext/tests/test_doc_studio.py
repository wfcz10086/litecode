"""skills/doc-studio: 配图需求解析/优先级/授权、html2pptx 坐标与样式映射、html2docx 结构、qa 规则、DAG 预设。
联网 (图库搜索) 一律打桩不真连; 浏览器 (playwright chromium) 不可用时相关用例 skip。"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SK = ROOT / "skills" / "doc-studio"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(mod):
    """临时把 scripts/ 放进 sys.path 加载, 加载完移除并清掉同目录的兄弟模块 (render/_ds 等名字太常见,
    留在 sys.modules / sys.path 里会让别的技能测试 `import render` 拿错模块)。"""
    sdir = str(SK / "scripts")
    mine = {p.stem for p in (SK / "scripts").glob("*.py")}
    hidden = {n: sys.modules.pop(n) for n in list(sys.modules) if n in mine}   # 别的技能同名模块先挪开
    sys.path.insert(0, sdir)
    try:
        spec = importlib.util.spec_from_file_location(f"_doc_studio_{mod}", SK / "scripts" / f"{mod}.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        sys.path.remove(sdir)
        for name, mm in list(sys.modules.items()):
            if (getattr(mm, "__file__", None) or "").startswith(sdir):
                del sys.modules[name]
        sys.modules.update(hidden)
    return m


images = load("images")
srcs = images.srcs
mk = images.mk


def _browser_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


BROWSER = _browser_ok()


def soup_of(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def args(**kw):
    a = images.build_parser().parse_args(["x.html", "--no-measure"])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class TestLicense(unittest.TestCase):
    def test_norm(self):
        cases = {"CC BY-SA 4.0": "by-sa", "CC BY 2.0": "by", "CC0": "cc0", "Public domain": "pd",
                 "pdm": "pd", "CC BY-NC-SA 2.0": "by-nc-sa", "cc-by-nd-3.0": "by-nd", "GFDL": "by-sa",
                 "": "unknown", "All rights reserved": "unknown"}
        for raw, want in cases.items():
            self.assertEqual(srcs.norm_license(raw), want, raw)

    def test_ok(self):
        self.assertTrue(srcs.license_ok("by-sa"))
        self.assertTrue(srcs.license_ok("cc0"))
        self.assertFalse(srcs.license_ok("by-nc"))
        self.assertTrue(srcs.license_ok("by-nc", allow_nc=True))
        self.assertFalse(srcs.license_ok("by-nd"))
        self.assertFalse(srcs.license_ok("unknown"))

    def test_label(self):
        self.assertEqual(srcs.license_label("pd"), "公有领域")
        self.assertEqual(srcs.license_label("by-sa", "CC BY-SA 4.0"), "CC BY-SA 4.0")

    def test_photo_filter_and_rank(self):
        good = {"title": "West Lake at dusk", "mime": "image/jpeg", "orig_width": 3000, "width": 3000, "height": 2000}
        self.assertTrue(srcs.plausible_photo(good))
        self.assertFalse(srcs.plausible_photo(dict(good, title="Map of Hangzhou")))
        self.assertFalse(srcs.plausible_photo(dict(good, mime="image/svg+xml")))
        self.assertFalse(srcs.plausible_photo(dict(good, orig_width=400)))
        tall = dict(good, title="tall", width=1000, height=2000)
        wide = dict(good, title="wide", width=1920, height=1080)
        self.assertEqual(srcs.rank([tall, wide], 16 / 9)[0]["title"], "wide")

    def test_wm_thumb(self):
        u = "https://upload.wikimedia.org/wikipedia/commons/0/07/A_b.jpg"
        self.assertEqual(srcs.wm_thumb(u, 2892), "https://thumb.wikimedia.org/wikipedia/commons/thumb/0/07/A_b.jpg/1920px-A_b.jpg")
        self.assertIn("/1280px-", srcs.wm_thumb(u, 1600))
        self.assertEqual(srcs.wm_thumb(u, 300), u)
        self.assertEqual(srcs.wm_thumb("https://x.org/a.jpg", 3000), "https://x.org/a.jpg")


class TestNeeds(unittest.TestCase):
    HTML = """<section class="slide">
      <img data-need="西湖断桥" data-kind="photo" data-query="West Lake Broken Bridge" data-ratio="16:9">
      <img data-need="已填" src="img/a.jpg">
      <div data-need="背景" data-kind="photo" id="hero"></div>
      <img data-need="游客量" data-kind="chart" data-chart='{"type":"bar","labels":["a"],"series":[{"data":[1]}]}'>
      <span data-need="交通" data-kind="icon" data-icon="map-pin"></span>
    </section>"""

    def test_parse_ratio(self):
        self.assertAlmostEqual(images.parse_ratio("16:9"), 16 / 9)
        self.assertAlmostEqual(images.parse_ratio("4/3"), 4 / 3)
        self.assertAlmostEqual(images.parse_ratio("1.5"), 1.5)
        self.assertIsNone(images.parse_ratio("abc"))
        self.assertIsNone(images.parse_ratio(None))

    def test_collect(self):
        needs = images.collect_needs(soup_of(self.HTML))
        self.assertEqual([n["kind"] for n in needs], ["photo", "photo", "photo", "chart", "icon"])
        self.assertTrue(needs[1]["skip"])                     # 已有 src 的不重取
        self.assertFalse(needs[0]["skip"])
        self.assertEqual(needs[2]["id"], "hero")
        self.assertEqual(needs[0]["query"], "West Lake Broken Bridge")
        self.assertAlmostEqual(needs[0]["ratio"], 16 / 9)
        self.assertFalse(images.collect_needs(soup_of(self.HTML), force=True)[1]["skip"])

    def test_pick_asset(self):
        d = Path(tempfile.mkdtemp())
        try:
            for n in ("west_lake_bridge.jpg", "office.png", "notes.txt"):
                (d / n).write_bytes(b"x")
            assets = images.list_assets([str(d)])
            self.assertEqual(len(assets), 2)
            need = {"desc": "断桥", "query": "West Lake bridge", "asset": ""}
            self.assertEqual(images.pick_asset(need, assets, set()).name, "west_lake_bridge.jpg")
            self.assertEqual(images.pick_asset(dict(need, asset="office.png"), assets, set()).name, "office.png")
            self.assertIsNone(images.pick_asset({"desc": "汽车", "query": "car", "asset": ""}, assets, set()))
        finally:
            shutil.rmtree(d)


class TestFill(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.orig = dict(srcs.SEARCHERS)

    def tearDown(self):
        srcs.SEARCHERS.clear()
        srcs.SEARCHERS.update(self.orig)
        shutil.rmtree(self.d)

    def _jpg(self, path, w=1600, h=1000):
        from PIL import Image
        Image.new("RGB", (w, h), (30, 120, 200)).save(path)
        return path

    def test_asset_beats_search(self):
        (self.d / "assets").mkdir()
        self._jpg(self.d / "assets" / "west_lake.jpg")
        srcs.SEARCHERS["wikimedia"] = lambda q: self.fail("用户素材命中时不应联网搜索")
        a = args(assets=[str(self.d / "assets")], sources="wikimedia")
        ctx = images.Ctx(a, self.d, {})
        r = images.fill_one({"id": "n1", "kind": "photo", "desc": "西湖", "query": "west lake", "ratio": 16 / 9,
                             "asset": "", "chart": None, "icon": ""}, ctx)
        self.assertEqual(r["via"], "asset")
        from PIL import Image
        w, h = Image.open(self.d / r["file"]).size
        self.assertAlmostEqual(w / h, 16 / 9, places=2)          # 按版面比例裁剪

    def test_search_license_and_fallback(self):
        img = self._jpg(self.d / "src.jpg")
        cands = [
            {"source": "wikimedia", "title": "NC photo", "url": "u-nc", "license": "by-nc", "mime": "image/jpeg", "width": 1600, "height": 1000},
            {"source": "wikimedia", "title": "Good photo", "url": "u-ok", "license": "by-sa", "license_raw": "CC BY-SA 4.0",
             "author": "Alice", "page_url": "https://commons/x", "mime": "image/jpeg", "width": 1600, "height": 1000},
        ]
        srcs.SEARCHERS["openverse"] = lambda q: (_ for _ in ()).throw(IOError("403"))
        srcs.SEARCHERS["wikimedia"] = lambda q: cands
        got = []
        orig_dl = srcs.download
        srcs.download = lambda url, dest, timeout=40: (got.append(url), shutil.copyfile(img, dest), dest)[2]
        try:
            ctx = images.Ctx(args(), self.d, {})
            n = {"id": "n1", "kind": "photo", "desc": "西湖", "query": "west lake", "ratio": 1.5, "asset": "", "chart": None, "icon": ""}
            r = images.fill_one(n, ctx)
            self.assertEqual(got, ["u-ok"])                       # NC 授权被过滤
            self.assertEqual((r["via"], r["author"], r["license_label"]), ("search", "Alice", "CC BY-SA 4.0"))
            self.assertEqual(ctx.source_status["openverse"]["fail"], 1)
            srcs.SEARCHERS["wikimedia"] = lambda q: []
            n2 = dict(n, id="n2", query="nothing")
            r2 = images.fill_one(n2, ctx)
            self.assertEqual((r2["status"], r2["via"]), ("fallback", "placeholder"))
            self.assertTrue((self.d / r2["file"]).exists())
        finally:
            srcs.download = orig_dl

    def test_illustration_without_comfy_degrades(self):
        srcs.SEARCHERS["openverse"] = lambda q: []
        srcs.SEARCHERS["wikimedia"] = lambda q: []
        ctx = images.Ctx(args(comfy=""), self.d, {"accent": "#2D8B8B"})
        r = images.fill_one({"id": "i1", "kind": "illustration", "desc": "插画", "query": "", "ratio": 1.5,
                             "asset": "", "chart": None, "icon": ""}, ctx)
        self.assertEqual(r["via"], "placeholder")

    def test_chart_and_icon(self):
        ctx = images.Ctx(args(), self.d, {"accent": "#2D8B8B"})
        spec = json.dumps({"type": "line", "labels": ["2021", "2022"], "series": [{"name": "a", "data": [1, 2]}]})
        r = images.fill_one({"id": "c1", "kind": "chart", "desc": "趋势", "query": "", "ratio": 16 / 9, "asset": "",
                             "chart": spec, "icon": "", "box": [640, 360]}, ctx)
        self.assertEqual(r["via"], "chart")
        from PIL import Image
        self.assertEqual(Image.open(self.d / r["file"]).size, (1280, 720))   # 2 倍清晰度
        bad = images.fill_one({"id": "c2", "kind": "chart", "desc": "x", "query": "", "ratio": 1.5, "asset": "",
                               "chart": "{oops", "icon": ""}, ctx)
        self.assertEqual(bad["status"], "failed")
        self.assertIn("map-pin", mk.icon_svg("location"))
        self.assertEqual(mk.guess_icon("西湖龙井茶"), "water")

    def test_apply_credits(self):
        html = """<section class="slide"><figure><img data-need="湖" data-kind="photo"><span data-credit></span></figure>
          <div class="footer"><span data-credit-page></span></div><span data-need="i" data-kind="icon" class="big"></span></section>
          <section class="slide"><div data-credits></div></section>"""
        s = soup_of(html)
        needs = images.collect_needs(s)
        res = {"need-01": {"status": "ok", "via": "search", "file": "img/need-01.jpg", "author": "Bob",
                           "license_label": "CC BY 2.0", "source": "wikimedia", "title": "Lake", "page_url": "https://c/x"},
               "need-02": {"status": "ok", "via": "icon", "icon": "sun"}}
        credits = images.apply(s, needs, res)
        self.assertEqual(len(credits), 1)
        self.assertEqual(s.img["src"], "img/need-01.jpg")
        self.assertEqual(s.select_one("[data-credit]").get_text(), "图: Bob · CC BY 2.0 · Wikimedia Commons")
        self.assertIn("Bob", s.select_one("[data-credit-page]").get_text())
        self.assertIn("https://c/x", s.select_one("[data-credits]").get_text())
        svg = s.find("svg")
        self.assertIn("big", svg["class"])
        self.assertEqual(svg["data-icon"], "sun")


class TestCrop(unittest.TestCase):
    def test_fit_crop(self):
        from PIL import Image
        d = Path(tempfile.mkdtemp())
        try:
            Image.new("RGB", (3000, 1000)).save(d / "a.jpg")
            info = mk.fit_crop(d / "a.jpg", d / "b.jpg", 1.0, max_w=800)
            self.assertEqual((info["width"], info["height"]), (800, 800))
            self.assertEqual(mk.css_tokens(":root{--ds-accent: #123456; --ds-text:#000;}"),
                             {"accent": "#123456", "text": "#000"})
        finally:
            shutil.rmtree(d)


PPTX_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font-family:"Noto Sans CJK SC",sans-serif}
section.slide{position:relative;width:1280px;height:720px;overflow:hidden;background:#FBFBF9}
.box{position:absolute;left:100px;top:80px;width:300px;height:200px;background:#2D8B8B;border-radius:20px}
h1{position:absolute;left:100px;top:320px;margin:0;font-size:48px;font-weight:800;color:#1F2A44;line-height:60px}
.p{position:absolute;left:100px;top:400px;width:400px;margin:0;font-size:20px;color:rgb(255,0,0);text-align:center}
.ph{position:absolute;left:700px;top:80px;width:400px;height:300px;overflow:hidden;border-radius:16px}
.ph img{width:100%;height:100%;object-fit:cover}
.grad{position:absolute;left:700px;top:420px;width:200px;height:100px;background:linear-gradient(90deg,#f00,#00f)}
table{position:absolute;left:100px;top:500px;border-collapse:collapse;font-size:16px}
td,th{padding:4px 10px;border-bottom:1px solid #ccc} th{background:#1F2A44;color:#fff}
.notes{display:none}
</style></head><body>
<section class="slide"><div class="box"></div><h1>标题 Title</h1><p class="p">红色<b>加粗</b>居中</p>
<div class="ph"><img src="wide.png"></div><div class="grad"></div>
<table><tr><th>A</th><th>B</th></tr><tr><td colspan="2">合并</td></tr></table>
<aside class="notes">备注内容</aside></section>
<section class="slide" style="background:#000"><p style="color:#fff;font-size:30px;margin:100px">第二页</p></section>
</body></html>"""


@unittest.skipUnless(BROWSER, "playwright chromium 不可用")
class TestHtml2Pptx(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import Image
        cls.d = Path(tempfile.mkdtemp())
        Image.new("RGB", (1600, 600), (200, 50, 50)).save(cls.d / "wide.png")
        (cls.d / "deck.html").write_text(PPTX_HTML, encoding="utf-8")
        cls.h2p = load("html2pptx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def test_editable(self):
        from pptx import Presentation
        from pptx.util import Emu
        rep = self.h2p.convert(str(self.d / "deck.html"), str(self.d / "e.pptx"), mode="editable")
        self.assertEqual(rep["modes"], ["editable", "editable"])
        prs = Presentation(str(self.d / "e.pptx"))
        self.assertEqual((prs.slide_width, prs.slide_height), (Emu(1280 * 9525), Emu(720 * 9525)))
        s1 = prs.slides[0]
        texts = {sh.text_frame.text: sh for sh in s1.shapes if sh.has_text_frame and sh.text_frame.text}
        h1 = texts["标题 Title"]
        self.assertAlmostEqual(h1.left / 9525, 100, delta=2)
        self.assertAlmostEqual(h1.top / 9525, 320, delta=8)
        r = h1.text_frame.paragraphs[0].runs[0]
        self.assertEqual(r.font.size.pt, 36)                     # 48px → 36pt
        self.assertTrue(r.font.bold)
        self.assertEqual(str(r.font.color.rgb), "1F2A44")
        self.assertFalse(h1.text_frame.word_wrap)                # 单行不折行
        p = texts["红色加粗居中"]
        runs = p.text_frame.paragraphs[0].runs
        self.assertEqual([x.text for x in runs], ["红色", "加粗", "居中"])
        self.assertTrue(runs[1].font.bold)
        self.assertEqual(str(runs[0].font.color.rgb), "FF0000")
        self.assertEqual(str(p.text_frame.paragraphs[0].alignment), "CENTER (2)")
        shapes = [sh for sh in s1.shapes if sh.shape_type == 1]  # AUTO_SHAPE
        box = next(sh for sh in shapes if abs(sh.left / 9525 - 100) < 2 and abs(sh.width / 9525 - 300) < 2)
        self.assertEqual(str(box.fill.fore_color.rgb), "2D8B8B")
        self.assertEqual(box.auto_shape_type, 5)                  # ROUNDED_RECTANGLE
        pics = [sh for sh in s1.shapes if sh.shape_type == 13]
        photo = next(pc for pc in pics if abs(pc.left / 9525 - 700) < 2 and abs(pc.top / 9525 - 80) < 2)
        self.assertAlmostEqual(photo.width / 9525, 400, delta=2)
        # 1600×600 cover 到 400×300: 缩放 0.5, 横向两边各裁 (800-400)/2/800 = 25%
        self.assertAlmostEqual(photo.crop_left, 0.25, places=2)
        self.assertAlmostEqual(photo.crop_right, 0.25, places=2)
        self.assertTrue(any(abs(pc.left / 9525 - 700) < 2 and abs(pc.top / 9525 - 420) < 2 for pc in pics))  # 渐变截图
        tbl = next(sh for sh in s1.shapes if sh.has_table).table
        self.assertEqual((len(tbl.rows), len(tbl.columns)), (2, 2))
        self.assertTrue(tbl.cell(1, 0).is_merge_origin)
        self.assertEqual(s1.notes_slide.notes_text_frame.text, "备注内容")
        s2 = prs.slides[1]
        self.assertEqual(str(s2.background.fill.fore_color.rgb), "000000")

    def test_image_mode(self):
        from pptx import Presentation
        rep = self.h2p.convert(str(self.d / "deck.html"), str(self.d / "i.pptx"), mode="image")
        self.assertEqual(rep["modes"], ["image", "image"])
        s = Presentation(str(self.d / "i.pptx")).slides[0]
        kinds = [sh.shape_type for sh in s.shapes]
        self.assertEqual(kinds, [17, 13])                         # 先隐藏文字框, 再整页图盖住
        self.assertIn("标题 Title", s.shapes[0].text_frame.text)
        self.assertIn("标题 Title", s.notes_slide.notes_text_frame.text)

    def test_auto_mode_rules(self):
        self.assertEqual(self.h2p.choose_mode({"metrics": {"items": 10}}, "auto")[0], "editable")
        self.assertEqual(self.h2p.choose_mode({"metrics": {"items": 300}}, "auto")[0], "image")
        self.assertEqual(self.h2p.choose_mode({"w": 100, "h": 100, "metrics": {"capFullArea": 9000}}, "auto")[0], "image")
        self.assertEqual(self.h2p.choose_mode({"error": "x"}, "auto")[0], "image")


DOC_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>:root{--ds-primary:#123456}</style></head><body>
<section class="page"><h1>标题</h1><p>正文 <b>加粗</b> 段落</p><h2>小节</h2>
<ul><li>一</li><li>二<ul><li>二.1</li></ul></li></ul>
<figure><img src="p.png"><figcaption>图 1 说明</figcaption></figure>
<table><tr><th>指标</th><th>值</th></tr><tr><td>a</td><td>1</td></tr></table>
<div class="grid-2"><div><h3>左</h3><p>L</p></div><div><h3>右</h3><p>R</p></div></div>
<div class="page-footer">页脚 1</div><aside class="notes">不要</aside><p data-docx="skip">跳过</p></section></body></html>"""


class TestHtml2Docx(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.d = Path(tempfile.mkdtemp())
        Image.new("RGB", (800, 400), (0, 100, 200)).save(self.d / "p.png")
        (self.d / "doc.html").write_text(DOC_HTML, encoding="utf-8")
        self.h2d = load("html2docx")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_python_engine(self):
        from docx import Document
        rep = self.h2d.convert(str(self.d / "doc.html"), str(self.d / "o.docx"), engine="python")
        self.assertEqual(rep["engine"], "python")
        doc = Document(str(self.d / "o.docx"))
        styles = [(p.style.name, p.text) for p in doc.paragraphs]
        self.assertIn(("Heading 1", "标题"), styles)
        self.assertIn(("Heading 2", "小节"), styles)
        self.assertIn(("List Bullet", "一"), styles)
        self.assertIn(("List Bullet 2", "二.1"), styles)
        self.assertIn(("Caption", "图 1 说明"), styles)
        body = next(p for p in doc.paragraphs if p.text.startswith("正文"))
        self.assertTrue(any(r.bold and r.text == "加粗" for r in body.runs))
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertEqual(len(doc.tables), 2)                     # 数据表 + 栅格转的无框表格
        self.assertEqual(doc.tables[0].cell(0, 0).text, "指标")
        self.assertEqual(doc.tables[1].cell(0, 1).paragraphs[0].text, "右")
        alltext = "\n".join(p.text for p in doc.paragraphs)
        for gone in ("不要", "跳过", "页脚 1"):
            self.assertNotIn(gone, alltext)
        self.assertEqual(str(doc.styles["Heading 1"].font.color.rgb), "123456")

    @unittest.skipUnless(shutil.which("pandoc"), "没有 pandoc")
    def test_pandoc_engine(self):
        from docx import Document
        rep = self.h2d.convert(str(self.d / "doc.html"), str(self.d / "p.docx"), engine="pandoc")
        self.assertEqual(rep["engine"], "pandoc")
        doc = Document(str(self.d / "p.docx"))
        self.assertGreaterEqual(rep["headings"], 2)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertNotIn("不要", "\n".join(p.text for p in doc.paragraphs))


QA_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0} section.slide{position:relative;width:1280px;height:720px;overflow:hidden;background:#fff;font-size:20px}
.card{position:absolute;left:50px;top:50px;width:200px;height:60px;background:#eee;overflow:hidden}
.a{position:absolute;left:400px;top:100px;font-size:30px}.b{position:absolute;left:410px;top:105px;font-size:30px}
.out{position:absolute;left:1200px;top:300px;white-space:nowrap}.tiny{position:absolute;left:50px;top:600px;font-size:9px}
.low{position:absolute;left:50px;top:650px;color:#eee}
</style></head><body>
<section class="slide"><div class="card"><p>很长很长的文字 很长很长的文字 很长很长的文字 很长很长的文字 很长很长的文字</p></div>
<div class="a">压住的文字</div><div class="b">另一段文字</div><div class="out">超出右边界的一段文字</div>
<div class="tiny">小字</div><div class="low">看不清</div><img src="missing.png" style="width:10px"></section>
<section class="slide"></section>
<section class="slide"><h2>正常页</h2><p>内容</p></section>
<section class="slide"><h2>图片问题页</h2><div class="photo0" style="background-image:url('dark.png')"></div>
<img src="dark.png" style="position:absolute;left:900px;top:500px;width:400px;height:400px">
<div style="position:absolute;left:0;top:200px;width:600px;height:300px;background-image:url('dark.png')">
<h1 style="margin:40px;color:#1D3B36;font-size:48px">深色字压在深色图上</h1></div></section>
</body></html>"""


@unittest.skipUnless(BROWSER, "playwright chromium 不可用")
class TestQa(unittest.TestCase):
    def test_rules(self):
        qa = load("qa")
        d = Path(tempfile.mkdtemp())
        try:
            from PIL import Image
            Image.new("RGB", (400, 400), (20, 30, 35)).save(d / "dark.png")
            (d / "deck.html").write_text(QA_HTML, encoding="utf-8")
            res = qa.check(str(d / "deck.html"), shots=str(d / "shots"))
            self.assertEqual(res["pages"], 4)
            self.assertEqual(len(res["shots"]), 4)
            p4 = [x["detail"] for x in res["issues"] if x["page"] == 4]
            self.assertTrue(any("没显示出来" in t for t in p4), p4)       # 0 高度的背景图容器
            self.assertTrue(any("超出页面" in t and "被裁掉" in t for t in p4), p4)
            self.assertTrue(any("图上文字" in t for t in p4), p4)          # 按截图像素算的对比度
            p1 = {(x["type"], x["severity"]) for x in res["issues"] if x["page"] == 1}
            for want in [("overflow", "error"), ("overlap", "error"), ("image", "error"), ("font", "warn"),
                         ("contrast", "warn")]:
                self.assertIn(want, p1)
            self.assertTrue(any("超出页面" in x["detail"] for x in res["issues"] if x["page"] == 1))
            self.assertTrue(any(x["type"] == "blank" and x["severity"] == "error" for x in res["issues"] if x["page"] == 2))
            self.assertFalse([x for x in res["issues"] if x["page"] == 3 and x["severity"] == "error"])
            self.assertFalse(res["ok"])
        finally:
            shutil.rmtree(d)


class TestSkillAndDag(unittest.TestCase):
    def test_frontmatter(self):
        from lib.skills import _frontmatter
        fm = _frontmatter((SK / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(fm["name"], "doc-studio")
        al = fm["aliases"]
        al = al if isinstance(al, list) else re.findall(r"[^\[\],\s]+", al)
        for a in ("做PPT", "幻灯片", "演示文稿", "做报告", "Word文档", "生成PDF", "海报", "宣传册", "长图"):
            self.assertIn(a, al)
        for s in ("pptx", "docx"):
            self.assertIn("doc-studio", (ROOT / "skills" / s / "SKILL.md").read_text(encoding="utf-8")[:1500])

    def test_dag_template(self):
        from lib.dag_schema import validate_dag_json
        d = json.loads((SK / "dag_doc_studio.json").read_text(encoding="utf-8"))
        ok, errs = validate_dag_json(d)
        self.assertTrue(ok, errs)
        ids = [s["id"] for s in d["steps"]]
        self.assertEqual(ids, ["outline", "html", "images", "pdf", "pptx", "qa", "report"])
        js = (ROOT / "web_assets" / "dag_editor_simple.js").read_text(encoding="utf-8")
        block = js[js.index("doc_studio: {"):js.index("/* 运行条件 when")]
        self.assertEqual(re.findall(r'"id": "(\w+)"', block), ids)
        self.assertIn("key: 'doc_studio'", (ROOT / "web_assets" / "dag_pipelines.js").read_text(encoding="utf-8"))
        for s in d["steps"]:
            for script in re.findall(r"doc-studio/scripts/(\w+\.py)", s["task"]):
                self.assertTrue((SK / "scripts" / script).exists(), script)

    def test_file_size_limit(self):
        for f in [p for p in (SK / "scripts").glob("*") if p.is_file()] + [SK / "SKILL.md"]:
            self.assertLessEqual(len(f.read_text(encoding="utf-8").splitlines()), 1000, f.name)


if __name__ == "__main__":
    unittest.main()
