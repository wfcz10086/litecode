"""内置知识库 plugins/kb: 切分 / 出处定位 / 关键词 / 融合打分与去重 / 上下文窗口 / 工具输出出处 /
文件路径与导入路径校验 / 接口鉴权; 分词与向量对齐 (有模型和 onnxruntime 时); PG 集成 (有库时)。

PG 集成测试连 LITECODE_KB_TEST_DSN (或仓库根 .env 里的 KB_PG_PASSWORD → 127.0.0.1:15432), 连不上就 skip;
只建/删以 zz-pytest- 开头的库, 不碰其他数据。向量用确定性的假编码替代, 不依赖模型。
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from plugins import kb as kbpkg  # noqa: E402
from plugins.kb import jobs, store, text, tool  # noqa: E402
from plugins.kb.store import Hit, fuse, windows  # noqa: E402


@pytest.fixture
def kbcfg(tmp_path, monkeypatch):
    """独立 config.json + 工作区, 返回改配置的函数。"""
    cfgp = tmp_path / "config.json"

    def set_cfg(kb=None, **extra):
        c = {"server": {"token": "tok-123"}, "web_ui": {"auth": {"enabled": True, "password": "pw"}}, **extra}
        if kb is not None:
            c["kb"] = kb
        cfgp.write_text(json.dumps(c))
        os.utime(cfgp, None)
        kbpkg._cache.update(mtime=None, cfg=None)
    monkeypatch.setenv("LITECODE_CONFIG", str(cfgp))
    monkeypatch.delenv("LITECODE_KB_DSN", raising=False)
    monkeypatch.setattr(kbpkg, "workspace", lambda: tmp_path / "ws")
    set_cfg()
    yield set_cfg
    kbpkg._cache.update(mtime=None, cfg=None)


# ── 切分 / 定位 / 关键词 ──────────────────────────────────────────

def _doc(n=40):
    return "".join(f"第{i}条规定: 员工应当遵守第{i}项制度, 违反者按情节处理。" for i in range(n))


def test_split_sizes_overlap_and_full_coverage():
    t = _doc(60)
    chunks = text.split(t, 450, 80)
    assert len(chunks) > 3
    for a, b, c in chunks[:-1]:
        assert 0 < b - a <= 450
        assert c.endswith("。")                      # 优先在句末断开
    for (a1, b1, _), (a2, b2, _) in zip(chunks, chunks[1:]):
        assert a2 < b1 and b1 - a2 <= 80             # 相邻块有重叠, 不超过 80 字
    covered = set()
    for a, b, _ in chunks:
        covered.update(range(a, b))
    assert covered >= set(range(len(t)))            # 没有漏掉的文字 (含文档末尾)
    assert "第59项制度" in chunks[-1][2]


def test_split_merges_short_tail_and_handles_no_punct():
    t = "甲" * 1000
    chunks = text.split(t, 450, 80)
    assert chunks[-1][1] == 1000 and all(b - a <= 450 * 1.25 for a, b, _ in chunks)
    assert text.split("短文本", 450, 80) == [(0, 3, "短文本")]


def test_join_and_locate_pages():
    segs = [text.Segment("第一页内容" * 10, "页", 1), text.Segment("", "页", 2), text.Segment("第三页内容" * 10, "页", 3)]
    full, spans = text.join(segs)
    assert full.count("\n\n") == 1                  # 空页跳过
    assert text.locate(spans, 0, 10) == ("第1页", 1)
    a = full.index("第三页")
    assert text.locate(spans, a - 5, a + 5) == ("第1-3页", 1)
    assert text.locate(spans, a + 1, a + 9) == ("第3页", 3)
    full2, spans2 = text.join([text.Segment("表格", label="工作表「Sheet1」")])
    assert text.locate(spans2, 0, 2) == ("工作表「Sheet1」", None)


def test_clean_fixes_pdf_radicals_and_fullwidth():
    assert text.clean("部⻔ ⻘松 ⼈⼒资源 ＡＢＣ") == "部门 青松 人力资源 ABC"


def test_terms_bigrams_and_query_stopwords():
    assert text.terms("KX-300 冷却泵") == ["kx-300", "冷却", "却泵"]
    q = text.terms("年假是多少天", query=True)
    assert "年假" in q and "多少" not in q and "是多" not in q and "假是" not in q


def test_parse_text_and_unsupported(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("第一段\n\n第二段", encoding="utf-8")
    segs = text.parse(p)
    assert [(s.text, s.num) for s in segs] == [("第一段", 1), ("第二段", 2)]
    g = tmp_path / "b.txt"
    g.write_bytes("国标编码".encode("gb18030"))
    assert text.parse(g)[0].text == "国标编码"
    with pytest.raises(RuntimeError, match="不支持"):
        text.parse(tmp_path / "x.exe")


# ── 融合打分 / 去重 / 窗口 ────────────────────────────────────────

def _hit(i, cos, terms, doc=1, start=None, end=None, h=None):
    s = i * 400 if start is None else start
    return Hit(i, doc, i, s, (s + 400) if end is None else end, f"c{i}", h or f"h{i}", f"第{i}段", None, "a.pdf",
               "库", cos, terms=set(terms))


def test_fuse_scores_threshold_and_dedup():
    q = ["年假", "天数"]
    hits = [_hit(0, 0.70, []), _hit(1, 0.66, ["年假", "天数"]), _hit(2, 0.66, ["年假", "天数"], h="h1"),
            _hit(3, 0.40, [])]
    out = fuse(hits, q, min_score=0.5, top_k=5)
    assert [h.chunk_id for h in out] == [1, 0]       # 关键词全覆盖的排到前面; 同内容 (h1) 只留一条; 低分被阈值滤掉
    assert out[0].score == round(0.75 * 0.66 + 0.25, 4)
    assert fuse(hits, q, 0.5, 1)[0].chunk_id == 1


def test_windows_expand_merge_and_budget():
    a, b, c = _hit(0, .9, [], start=1000, end=1400), _hit(1, .8, [], start=1300, end=1700), _hit(2, .7, [], doc=2, start=0, end=400)
    for h in (a, b, c):
        h.score = h.cos
    ws = windows([a, b, c], {1: 10000, 2: 500}, 3000)
    assert len(ws) == 2
    w1 = ws[0]
    assert w1["doc_id"] == 1 and w1["end"] - w1["start"] == 3000 and len(w1["hits"]) == 2   # b 被 a 的窗口覆盖, 不重复
    assert ws[1] == {"doc_id": 2, "start": 0, "end": 500, "hits": [c]}                     # 短文档整篇
    far = _hit(3, .6, [], start=5000, end=5400)
    far.score = .6
    ws = windows([a, far], {1: 10000}, 3000, total_chars=4000)
    assert sum(w["end"] - w["start"] for w in ws) <= 4000                                   # 总字数上限
    near = _hit(4, .6, [], start=2900, end=3300)
    near.score = .6
    ws = windows([a, near], {1: 10000}, 3000)
    assert len(ws) == 1 and ws[0]["start"] == 0 and ws[0]["end"] > 3300                    # 相接的窗口合并, 文字不重复
    tail = _hit(5, .9, [], start=9700, end=10000)
    tail.score = .9
    w = windows([tail], {1: 10000}, 3000)[0]
    assert (w["start"], w["end"]) == (7000, 10000)                                          # 文档末尾的块: 向前补足


def test_tool_output_has_numbered_sources(kbcfg):
    d = {"bases": ["员工手册"], "results": [
        {"filename": "手册.pdf", "loc": "第3页", "also": "第5页", "score": 0.81, "context": "一线城市每晚 650 元",
         "head": True, "tail": False},
        {"filename": "规程.docx", "loc": "第2段", "score": 0.6, "snippet": "扭矩 42 牛·米"}]}
    s = tool.format_results("住宿标准", d)
    assert "[1] 手册.pdf · 第3页 (同文另见 第5页) · 相关度 0.81" in s and "[2] 规程.docx · 第2段" in s
    assert "…一线城市每晚 650 元" in s and "[编号]" in s and s.rstrip().endswith("[2] 规程.docx 第2段")
    assert "没有找到" in tool.format_results("x", {"bases": ["员工手册"], "results": []})
    assert tool.format_results("x", {"error": "坏了"}) == "ERROR: 坏了"


def test_tool_disabled_without_config(kbcfg):
    assert kbpkg.enabled() is False
    assert "未启用" in asyncio.run(tool.run({"query": "年假"}))
    kbcfg({"dsn": "postgresql://u:p@127.0.0.1:1/x"})
    assert kbpkg.enabled() is True                   # 有 dsn 未写 enabled → 视为启用
    kbcfg({"enabled": False, "dsn": "postgresql://u:p@127.0.0.1:1/x"})
    assert kbpkg.enabled() is False


# ── 文件路径 / 接口鉴权 / 导入路径校验 ──────────────────────────────

def test_file_path_validation(kbcfg):
    h = "a" * 64
    p = jobs.file_path(3, h, ".pdf")
    assert p.name == h + ".pdf" and p.parent.name == "files" and p.parent.parent.name == "3"
    for bad_hash, bad_ext in [("../../etc/passwd", ".pdf"), ("A" * 64, ".pdf"), (h, ".p/df"), (h, "../x")]:
        with pytest.raises(ValueError):
            jobs.file_path(3, bad_hash, bad_ext)
    assert jobs.norm_ext("报告.PDF") == ".pdf" and jobs.norm_ext("x.tar.gz/../a") == ""


@pytest.fixture
def client(kbcfg):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from plugins.auth import core as auth
    from plugins.kb.router import router
    old = (auth._AUTH_ENABLED, auth._AUTH_PASSWORD)
    auth._AUTH_ENABLED = True
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)
    auth._AUTH_ENABLED, auth._AUTH_PASSWORD = old


def test_router_requires_login(client):
    for method, url in [("get", "/api/kb/status"), ("get", "/api/kb/bases"), ("post", "/api/kb/bases"),
                        ("delete", "/api/kb/bases/1"), ("get", "/api/kb/docs/1/file"), ("post", "/api/kb/search")]:
        r = getattr(client, method)(url, **({"json": {"query": "x"}} if method == "post" else {}))
        assert r.status_code == 401, (method, url, r.status_code)


def test_router_internal_token_only_for_search(client):
    r = client.post("/api/kb/search", json={"query": "x"}, headers={"X-LiteCode-Token": "tok-123"})
    assert r.status_code == 200 and "未启用" in r.json()["error"]
    assert client.post("/api/kb/search", json={"query": "x"}, headers={"X-LiteCode-Token": "wrong"}).status_code == 401
    assert client.get("/api/kb/bases", headers={"X-LiteCode-Token": "tok-123"}).status_code == 401


def test_import_path_rejects_outside_workspace(client, kbcfg, tmp_path, monkeypatch):
    from plugins.auth import core as auth
    monkeypatch.setattr(auth, "require_auth", lambda request: None)
    kbcfg({"dsn": "postgresql://u:p@127.0.0.1:1/x"})
    ws = tmp_path / "ws"
    (ws / "kb" / "1" / "files").mkdir(parents=True)
    (ws / "kb" / "1" / "files" / "x.pdf").write_bytes(b"x")
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    for p in [str(outside), "../secret.txt", "/etc/passwd", str(ws / "kb" / "1" / "files" / "x.pdf"), ""]:
        r = client.post("/api/kb/bases/1/import_path", json={"path": p})
        assert r.status_code == 400, (p, r.status_code, r.text)
    assert client.post("/api/kb/bases/1/import_path", json={"path": "uploads/none.pdf"}).status_code == 404


# ── 分词 / 向量与原模型对齐 (需要导出的模型目录) ──────────────────────

def _model_dir():
    for d in [os.environ.get("LITECODE_KB_MODEL_DIR"),
              ROOT.parent / "workspace/kb/models/text2vec-base-chinese-paraphrase-onnx",
              Path("/tmp/litecode_workspace/kb/models/text2vec-base-chinese-paraphrase-onnx")]:
        if d and (Path(d) / "selftest.json").is_file():
            return Path(d)
    return None


def test_tokenizer_matches_transformers():
    d = _model_dir()
    if d is None:
        pytest.skip("没有导出的向量模型 (scripts/kb_export_onnx.sh)")
    from plugins.kb.embed import WordPiece
    st = json.loads((d / "selftest.json").read_text(encoding="utf-8"))
    wp = WordPiece(d / "vocab.txt")
    for t, ids in zip(st["texts"], st["ids"]):
        assert wp.encode(t) == ids, t


def test_embedding_matches_reference():
    d = _model_dir()
    if d is None:
        pytest.skip("没有导出的向量模型")
    pytest.importorskip("onnxruntime")
    import numpy as np
    from plugins.kb.embed import Embedder
    st = json.loads((d / "selftest.json").read_text(encoding="utf-8"))
    v = Embedder(d).encode(st["texts"])
    ref = np.array(st["ref"])
    ref /= np.linalg.norm(ref, axis=1, keepdims=True)
    assert float((v * ref).sum(1).min()) > 0.9999


# ── PG 集成 (真实 kb-pg) ───────────────────────────────────────────

def _test_dsn():
    dsn = os.environ.get("LITECODE_KB_TEST_DSN", "")
    if not dsn:
        envf = ROOT.parent / ".env"
        if envf.is_file():
            for ln in envf.read_text().splitlines():
                if ln.startswith("KB_PG_PASSWORD="):
                    dsn = f"postgresql://litecode_kb:{ln.split('=', 1)[1].strip()}@127.0.0.1:15432/litecode_kb"
    if not dsn:
        return None
    try:
        import psycopg2
        psycopg2.connect(dsn, connect_timeout=2).close()
        return dsn
    except Exception:
        return None


def _fake_encode(texts):
    import numpy as np
    out = np.zeros((len(texts), 768), dtype="float32")
    for i, t in enumerate(texts):
        for w in text.terms(t):
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 768] += 1
        out[i, 0] += 0.01
    return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture
def pg(kbcfg, monkeypatch):
    dsn = _test_dsn()
    if not dsn:
        pytest.skip("没有可用的 kb-pg (设 LITECODE_KB_TEST_DSN)")
    kbcfg({"enabled": True, "dsn": dsn, "ocr": False, "min_score": 0.1})   # 假向量分数偏低
    from plugins.kb import embed
    monkeypatch.setattr(embed, "encode", _fake_encode)
    store._pool.update(dsn=None, pool=None, schema=False)

    def cleanup():
        for b in store.list_bases():
            if b["name"].startswith("zz-pytest-"):
                store.delete_base(b["id"])
                jobs.remove_kb_dir(b["id"])
    cleanup()
    yield store
    cleanup()


def test_pg_ingest_search_dedup_and_delete(pg, tmp_path):
    base = pg.create_base("zz-pytest-手册", "测试")
    with pytest.raises(store.KBError):
        pg.create_base("zz-pytest-手册")
    src = tmp_path / "手册.md"
    body = "\n\n".join(f"第{i}节 普通条款: 员工应遵守公司第{i}号制度, 按流程办理各项手续。" for i in range(80))
    src.write_text(body + "\n\n附则: 手册档案编号为 XH-HR-2026-0917, 应急联络口令为青松七四二九。", encoding="utf-8")
    h, ext = jobs.sha256_file(src), ".md"
    jobs.store_file(base["id"], src, h, ext, move=False)
    doc, created = pg.add_document(base["id"], src.name, ext, h, src.stat().st_size)
    assert created and pg.add_document(base["id"], src.name, ext, h, 1)[1] is False     # 同库同内容去重
    claimed = pg.claim_next()                                # 跑这个测试时别开着别的入库进程, 否则可能被它认领
    assert claimed and claimed["id"] == doc["id"] and claimed["status"] == "parsing"
    assert pg.claim_next() is None                           # 已认领的不会被再次认领
    jobs.process(claimed)
    d = pg.get_document(doc["id"])
    assert d["status"] == "done" and d["chunk_count"] > 5, d
    res = pg.search("应急联络口令是什么", [base["id"]], top_k=3, min_score=0.1)
    assert res and "青松七四二九" in res[0]["context"] and res[0]["filename"] == "手册.md"
    assert res[0]["loc"].startswith("第") and res[0]["hit_end"] == d["text_len"]          # 文档末尾的块能检出
    assert len(res[0]["context"]) <= kbpkg.cfg()["context_chars"]
    out = tool.format_results("应急联络口令", tool.do_search("应急联络口令是什么", "zz-pytest-手册", 2))
    assert out.startswith("知识库「zz-pytest-手册」") and "[1] 手册.md · 第" in out
    assert pg.retry_document(doc["id"]) and pg.get_document(doc["id"])["status"] == "queued"
    gone = pg.delete_document(doc["id"])
    jobs.remove_file(gone["kb_id"], gone["content_hash"], gone["ext"])
    assert not jobs.file_path(base["id"], h, ext).exists()
    assert pg.search("应急联络口令", [base["id"]]) == []
    assert pg.delete_base(base["id"])
