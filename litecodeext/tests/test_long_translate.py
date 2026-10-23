"""long-translate 技能 (skills/long-translate/scripts) 离线测试: 切块 / 术语注入 / 续跑 / 格式保持。

模型调用全部走 httpx.MockTransport, 不联网。
"""
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "long-translate" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import long_translate as LT  # noqa: E402
import lt_core as C  # noqa: E402
import lt_formats as F  # noqa: E402
import lt_llm  # noqa: E402

MARK = re.compile(r"(⟨\d+⟩)")


# ── 假模型 ────────────────────────────────────────────────────
def fake_translate(text: str) -> str:
    """保留占位符/标记, 其余每 3 个字符变 1 个汉字 (英→中典型长度比)。"""
    return "".join(p if MARK.fullmatch(p) else ("译" * (len(p.strip()) // 3 + 1) if p.strip() else p)
                   for p in MARK.split(text))


class FakeServer:
    def __init__(self, drop_first=None, fail_on=None):
        self.calls = []
        self.drop_first = drop_first          # 第一次调用时故意漏掉的段号 (1-based)
        self.fail_on = fail_on                # 原文含此字符串的请求返回 500
        self.dropped = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        self.calls.append(user)
        if self.fail_on and self.fail_on in user:
            return httpx.Response(400, json={"error": "boom"})
        part = user.split("【待翻译】", 1)[1]
        segs = C.parse_marked(part.split("\n", 1)[1], 10 ** 6)
        out = []
        for k, t in sorted(segs.items()):
            if self.drop_first == k and not self.dropped:
                self.dropped = True
                continue
            out.append(f"⟪{k}⟫\n{fake_translate(t)}")
        return httpx.Response(200, json={"choices": [{"message": {"content": "\n\n".join(out)}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": 10}})


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"model": {"id": "fake", "backend_url": "http://fake/v1", "api_key": "",
                                       "context_window": 32000, "max_tokens": 4096}}))
    return str(p)


@pytest.fixture
def server(monkeypatch):
    srv = FakeServer()
    orig = lt_llm.LLM.__init__

    def init(self, model, **kw):
        kw["transport"] = httpx.MockTransport(srv.handler)
        kw["retries"] = 0
        orig(self, model, **kw)
    monkeypatch.setattr(lt_llm.LLM, "__init__", init)
    return srv


def args(**kw):
    base = dict(config="", model="", jobs=2, thinking="low", temperature=None, limit=0, redo="", src_lang="auto",
                to="zh", chunk_tokens=0, style="", reset=False, workdir="", no_glossary=True, fix_terms=False,
                window_tokens=6000, min_count=2, overwrite=False, output="", no_polish=False)
    base.update(kw)
    return type("A", (), base)()


def make_book(n_ch=6, paras=8) -> str:
    out = []
    for c in range(1, n_ch + 1):
        out.append(f"CHAPTER {c}.")
        for p in range(paras):
            out.append(f"Elizabeth walked to Netherfield in chapter {c}, paragraph {p}. " * 6)
    return "\n\n".join(out) + "\n"


# ── 切块 ──────────────────────────────────────────────────────
def test_chunks_respect_budget_and_chapter_boundaries():
    segs = F.extract_plain(make_book(), False)
    for i, s in enumerate(segs):
        s["i"] = i
    chunks = C.make_chunks(segs, 600)
    assert sum(len(c["segs"]) for c in chunks) == len(segs)            # 不丢段, 不重复
    flat = [i for c in chunks for i in c["segs"]]
    assert flat == list(range(len(segs)))
    for c in chunks:
        assert c["tokens"] <= 600 * 1.3
    heads = [i for i, s in enumerate(segs) if s["kind"] == "heading"]
    assert len(heads) == 6
    starts = {c["segs"][0] for c in chunks}
    assert sum(h in starts for h in heads[1:]) >= 2
    starts = {c["segs"][0] for c in C.make_chunks(segs, 600, min_ratio=0.15)}
    assert all(h in starts for h in heads)                              # 块已过 15% 就在章节标题处断开


def test_long_paragraph_is_split_and_merged_back():
    long_p = "This is a sentence that goes on. " * 400
    segs = F.extract_plain("Intro.\n\n" + long_p + "\n\nEnd.\n", False)
    for i, s in enumerate(segs):
        s["i"] = i
    ex = C.explode_long_segments(segs, 300)
    assert len(ex) > 4 and all(C.est_tokens(s["text"]) <= 300 for s in ex)
    assert sum(1 for s in ex if s.get("cont")) == len(ex) - 3
    trans = {s["i"]: f"[{s['i']}]" for s in ex}
    merged = F.merge_continuations(ex, trans, cjk_tgt=True)
    assert len(merged) == 3
    out = F.rebuild_text(ex, merged)
    assert out.count("\n\n") == 2 and out.endswith("\n")
    # 没有译文时整段原文回落, 不丢内容
    assert F.rebuild_text(ex, {}).split() == ("Intro.\n\n" + long_p + "\n\nEnd.\n").split()


def test_small_tail_chunk_merged():
    segs = [{"i": i, "text": "word " * 100, "kind": "p"} for i in range(7)]
    chunks = C.make_chunks(segs, 300)
    assert chunks[-1]["tokens"] > 300 * 0.25


# ── 术语 ──────────────────────────────────────────────────────
def test_term_matcher_word_boundary_and_injection():
    terms = [{"src": "Darcy", "dst": "达西"}, {"src": "Mr. Darcy", "dst": "达西先生"}, {"src": "Bath", "dst": "巴斯"}]
    m = C.TermMatcher(terms)
    hits = [t["src"] for t in m.hits("Mr. Darcy took a bath; Darcyville is far.")]
    assert hits == ["Mr. Darcy"]                                        # 长词优先, bath 小写/Darcyville 不算
    prompt = C.build_user_prompt(["Mr. Darcy smiled."], m.hits("Mr. Darcy smiled."))
    assert "Mr. Darcy → 达西先生" in prompt and "巴斯" not in prompt     # 只注入本块命中的
    assert "⟪1⟫" in prompt


def test_merge_candidates_and_harmonize_names():
    text = "Mr. Bennet and Mrs. Bennet. Elizabeth Bennet laughed. Elizabeth Bennet. Mr. Bennet. Long ago, long road."
    cands = [{"src": "Mr. Bennet", "dst": "班纳特先生", "type": "person"},
             {"src": "Mr. Bennet", "dst": "班纳特先生", "type": "person"},
             {"src": "Mr. Bennet", "dst": "贝内特先生", "type": "person"},
             {"src": "Elizabeth Bennet", "dst": "伊丽莎白·贝内特", "type": "person"},
             {"src": "Mrs. Long", "dst": "朗太太", "type": "person"}]
    terms = C.merge_term_candidates(cands, text, min_count=1)
    mb = next(t for t in terms if t["src"] == "Mr. Bennet")
    assert mb["dst"] == "班纳特先生" and "候选" in mb["note"]
    terms = C.harmonize_names(terms, text)
    eb = next(t for t in terms if t["src"] == "Elizabeth Bennet")
    assert eb["dst"] == "伊丽莎白·班纳特" and "已统一" in eb["note"]
    assert any(t["src"] == "Bennet" and t["dst"] == "班纳特" for t in terms)
    assert not any(t["src"] == "Long" for t in terms)                   # 普通词不补成术语


def test_glossary_tsv_roundtrip(tmp_path):
    p = tmp_path / "g.tsv"
    C.save_glossary_tsv(p, [{"src": "Darcy", "dst": "达西", "type": "person", "count": 3, "note": "x"},
                            {"src": "Empty", "dst": "", "type": ""}])
    got = C.load_glossary_tsv(p)
    assert [t["src"] for t in got] == ["Darcy"] and got[0]["count"] == 3


# ── 解析 / 质检 ───────────────────────────────────────────────
def test_parse_marked_and_too_short():
    got = C.parse_marked("⟪1⟫\n你好\n\n⟪2⟫ 世界\n⟪2⟫ 重复", 2)
    assert got == {1: "你好", 2: "世界"}
    src = "It was a bright cold day in April, and the clocks were striking thirteen."
    assert C.too_short(src, "四月", "en", "zh")
    assert not C.too_short(src, "四月里一个晴朗而寒冷的日子，时钟正敲响十三点。", "en", "zh")
    assert C.too_short("短句", "", "en", "zh")                              # 短段也不允许空译文


def test_restore_placeholders_reports_lost():
    t, lost = C.restore_placeholders("见 ⟨0⟩", {"⟨0⟩": "`x`", "⟨1⟩": "http://a"})
    assert lost == ["⟨1⟩"] and "`x`" in t and "http://a" in t


# ── 续跑 / 并发 / 重试 ─────────────────────────────────────────
def test_translate_resume_skips_done_chunks(tmp_path, cfg, server):
    src = tmp_path / "book.txt"
    src.write_text(make_book(), encoding="utf-8")
    w = LT.cmd_init(args(src=str(src), workdir=str(tmp_path / "w"), chunk_tokens=500, config=cfg))
    n = len(w.chunks())
    assert n >= 5
    LT.cmd_translate(args(workdir=str(w.d), limit=2, config=cfg), w)          # 模拟中途停止: 只翻了 2 块
    assert len(list(w.out.glob("chunk_*.json"))) == 2
    first = len(server.calls)
    LT.cmd_translate(args(workdir=str(w.d), config=cfg), w)                  # 续跑
    assert len(server.calls) - first == n - 2                                 # 已完成的块不再调用
    assert all(w.done(c["id"]) for c in w.chunks())
    LT.cmd_translate(args(workdir=str(w.d), config=cfg), w)                  # 全部完成再跑 = 0 调用
    assert len(server.calls) - first == n - 2
    out = LT.cmd_build(args(workdir=str(w.d)), w)
    text = out.read_text(encoding="utf-8")
    assert "Elizabeth" not in text and text.count("\n\n") == make_book().count("\n\n")
    assert (w.d / "qa_report.md").exists()
    st = json.loads((w.d / "stats.json").read_text())
    assert st["translate"]["calls"] == n


def test_reinit_same_source_keeps_progress_and_rejects_changed(tmp_path, cfg, server):
    src = tmp_path / "book.txt"
    src.write_text(make_book(), encoding="utf-8")
    a = args(src=str(src), workdir=str(tmp_path / "w"), chunk_tokens=500, config=cfg)
    w = LT.cmd_init(a)
    LT.cmd_translate(args(workdir=str(w.d), limit=1, config=cfg), w)
    LT.cmd_init(a)                                                            # 同一原文: 沿用
    assert len(list(w.out.glob("chunk_*.json"))) == 1
    src.write_text(make_book(3), encoding="utf-8")
    with pytest.raises(SystemExit):
        LT.cmd_init(a)                                                        # 原文变了: 拒绝混用


def test_missing_segment_triggers_retry_with_feedback(tmp_path, cfg, server):
    server.drop_first = 2
    src = tmp_path / "s.txt"
    src.write_text("First paragraph here is long enough to matter.\n\nSecond paragraph that the model skips.\n\n"
                   "Third paragraph closes it out nicely.\n", encoding="utf-8")
    w = LT.cmd_init(args(src=str(src), workdir=str(tmp_path / "w"), config=cfg))
    LT.cmd_translate(args(workdir=str(w.d), config=cfg), w)
    assert len(server.calls) == 2 and "漏了第 2 段" in server.calls[1]
    rec = w.done(0)
    assert all(rec["trans"][str(i)] for i in range(3)) and not rec["warnings"]


def test_failed_chunk_is_retried_on_next_run(tmp_path, cfg, server):
    server.fail_on = "chapter 3"
    src = tmp_path / "book.txt"
    src.write_text(make_book(), encoding="utf-8")
    w = LT.cmd_init(args(src=str(src), workdir=str(tmp_path / "w"), chunk_tokens=500, config=cfg))
    errs = LT.cmd_translate(args(workdir=str(w.d), config=cfg), w)
    assert errs and not all(w.done(c["id"]) for c in w.chunks())
    server.fail_on = None
    assert not LT.cmd_translate(args(workdir=str(w.d), config=cfg), w)
    assert all(w.done(c["id"]) for c in w.chunks())


def test_redo_marks_and_retranslates(tmp_path, cfg, server):
    src = tmp_path / "book.txt"
    src.write_text(make_book(2, 3), encoding="utf-8")
    w = LT.cmd_init(args(src=str(src), workdir=str(tmp_path / "w"), config=cfg))
    LT.cmd_translate(args(workdir=str(w.d), config=cfg), w)
    k = len(server.calls)
    LT.cmd_translate(args(workdir=str(w.d), redo="0", config=cfg), w)
    assert len(server.calls) == k + 1


def test_config_empty_key_and_served_name(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"model": {"id": "x"}, "models": [
        {"id": "m1", "backend_url": "http://h/v1/", "api_key": "", "served_model_name": "Real"}]}))
    m = lt_llm.load_model(str(p), "m1")
    assert m["key"] == "EMPTY" and m["wire"] == "Real" and m["url"] == "http://h/v1"


# ── 格式保持 ───────────────────────────────────────────────────
MD = """---
title: Demo
---

# Getting Started

Read the [guide](https://example.com/g "G") or run `pip install w` now.

1. Download from https://example.com/dl.
2. Run it.

```python
print("keep me")
```

| Name | Desc |
|------|------|
| Gear | A toothed wheel |

> Note: needs Python.
"""


def _identity(segs):
    return {s["i"]: C.restore_placeholders(s["text"], s.get("ph"))[0] for s in segs if C.translatable(s)}


def test_md_roundtrip_identity_and_protection():
    segs = F.extract_md(MD)
    for i, s in enumerate(segs):
        s["i"] = i
    assert F.rebuild_text(segs, _identity(segs)) == MD                     # 结构原样还原
    kinds = {s["kind"] for s in segs}
    assert "code" in kinds and "heading" in kinds
    joined = " ".join(s["text"] for s in segs if C.translatable(s))
    assert "https://example.com" not in joined and "pip install" not in joined   # 链接/行内代码已保护
    assert "print(" not in joined
    fake = {i: fake_translate(t) for i, t in ((s["i"], s["text"]) for s in segs if C.translatable(s))}
    final = {i: C.restore_placeholders(t, segs[i].get("ph"))[0] for i, t in fake.items()}
    out = F.rebuild_text(segs, final)
    assert '](https://example.com/g "G")' in out and "`pip install w`" in out and 'print("keep me")' in out
    assert "https://example.com/dl" in out and out.startswith("---\ntitle: Demo\n---")
    assert re.search(r"^# 译", out, re.M) and re.search(r"^1\. 译", out, re.M) and "|------|------|" in out


def test_srt_keeps_timing():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n2\n00:00:03,000 --> 00:00:04,000\nTwo\nlines.\n\n3\n00:00:05,000 --> 00:00:06,000\n♪♪\n\n"
    segs = F.extract_srt(srt)
    for i, s in enumerate(segs):
        s["i"] = i
    assert F.rebuild_text(segs, _identity(segs)) == srt
    out = F.rebuild_text(segs, {s["i"]: "译文" for s in segs if C.translatable(s)})
    assert out.count("-->") == 3 and "00:00:03,000 --> 00:00:04,000\n译文\n" in out and "♪♪" in out


def test_txt_reflow_and_headings():
    txt = "CHAPTER I.\n\nIt is a truth universally acknowledged, that a single man in possession\nof a good fortune must be in want of a wife.\n\n第三章 风起\n\nShort\nverse\nlines\n"
    segs = F.extract_plain(txt, False)
    assert segs[0]["kind"] == "heading" and segs[2]["kind"] == "heading"
    assert "\n" not in segs[1]["text"] and segs[3]["text"] == "Short\nverse\nlines"


def test_pdf_paragraph_rebuild():
    lines = ["It is a truth universally acknowledged, that a single man in possession",
             "of a good fortune must be in want of a wife.",
             "However little known the feelings or views of such a man may be on his",
             "first entering a neighbourhood."]
    out = F._pdf_paragraphs("\n".join(lines))
    assert len([b for b in out.split("\n\n") if b.strip()]) == 2


def test_docx_styles_and_bold_runs(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("Chapter One", level=1)
    p = d.add_paragraph("The wind howled as ")
    p.add_run("Captain Ahab").bold = True
    p.add_run(" stared at the sea.")
    d.add_paragraph("Quiet line.", style="Quote")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Ship"
    t.cell(0, 1).text = "Old and tired."
    src = tmp_path / "b.docx"
    d.save(str(src))
    fmt, segs = F.extract(src)
    assert fmt == "docx" and segs[0]["kind"] == "heading"
    rich = next(s for s in segs if s.get("runs"))
    assert rich["text"].count("⟨") == 2
    final = {s["i"]: fake_translate(s["text"]) for s in segs if C.translatable(s)}
    out = tmp_path / "o.docx"
    F.write_output(fmt, src, segs, final, out)
    d2 = docx.Document(str(out))
    assert d2.paragraphs[0].style.name == "Heading 1" and d2.paragraphs[2].style.name == "Quote"
    runs = [(r.text, r.bold) for r in d2.paragraphs[1].runs]
    assert runs[1][1] is True and runs[1][0].startswith("译") and runs[0][1] is not True
    assert "Captain" not in d2.paragraphs[1].text and d2.tables[0].cell(0, 1).text.startswith("译")


def _make_epub(path: Path):
    x = '<?xml version="1.0" encoding="utf-8"?>\n'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", x + '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/c.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/c.opf", x + '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:language>en</dc:language></metadata><manifest>'
                   '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" '
                   'media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        for n, body in (("a", '<h1>Chapter 1</h1><p>A <i>bright</i> day &amp; clocks.<a href="#n1"><sup>1</sup></a></p>'),
                        ("b", "<h1>Chapter 2</h1><p>Cabbage smell.</p><pre>code stays</pre>")):
            z.writestr(f"OEBPS/{n}.xhtml", x + f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>{body}</body></html>')
        z.writestr("OEBPS/img.png", b"\x89PNG fake")


def test_epub_per_chapter_inline_tags(tmp_path):
    pytest.importorskip("lxml")
    src = tmp_path / "b.epub"
    _make_epub(src)
    fmt, segs = F.extract(src)
    assert fmt == "epub" and [s["loc"][0] for s in segs if s.get("brk")] == ["OEBPS/a.xhtml", "OEBPS/b.xhtml"]
    p = next(s for s in segs if "bright" in s["text"])
    assert "<i>" not in p["text"] and "&amp;" not in p["text"] and "&" in p["text"]
    chunks = C.make_chunks(segs, 30)
    assert len(chunks) == 2
    assert chunks[1]["segs"][0] == next(s["i"] for s in segs if s.get("brk") and s["loc"][0].endswith("b.xhtml"))
    import html as H
    final = {}
    for s in segs:
        if C.translatable(s):
            final[s["i"]] = C.restore_placeholders(H.escape(fake_translate(s["text"]) + " &", quote=False), s.get("ph"))[0]
    out = tmp_path / "o.epub"
    F.write_output(fmt, src, segs, final, out, "zh")
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        a = z.read("OEBPS/a.xhtml").decode()
        assert "<i>译" in a and "<a href=\"#n1\"><sup>" in a and "&amp;" in a and "bright" not in a
        assert "code stays" in z.read("OEBPS/b.xhtml").decode()
        assert "<dc:language>zh</dc:language>" in z.read("OEBPS/c.opf").decode()
        assert z.read("OEBPS/img.png") == b"\x89PNG fake"


def test_epub_broken_placeholders_fall_back_to_text(tmp_path):
    pytest.importorskip("lxml")
    src = tmp_path / "b.epub"
    _make_epub(src)
    fmt, segs = F.extract(src)
    p = next(s for s in segs if "bright" in s["text"])
    bad = {p["i"]: "<i>未闭合 标签"}
    out = tmp_path / "o.epub"
    F.write_output(fmt, src, segs, bad, out)
    with zipfile.ZipFile(out) as z:
        assert "未闭合 标签" in z.read("OEBPS/a.xhtml").decode()
