"""long-translate 质检: 术语一致性 / 漏译残留检查 (check)、抽检打分 (review)、第二遍润色 (polish)、质检报告。"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path

import lt_core as C
from lt_llm import LLM, Usage, load_model

LATIN_RUN = re.compile(r"(?:[A-Za-z][A-Za-z'’\-]*[\s,;:]+){6,}[A-Za-z]+")


def _alts(note: str) -> list[str]:
    """从 glossary 备注里取"别的译法": '候选: 达西×5 / 达尔西×1' → [达西, 达尔西]; '已统一 X→宾利 (原 彬格莱)' → [彬格莱]"""
    out = []
    m = re.search(r"候选:([^已]+)", note or "")
    if m:
        out += [x.split("×")[0].strip() for x in m.group(1).split("/") if x.strip()]
    out += re.findall(r"\(原 ([^)]+)\)", note or "")
    return [x for x in out if x]


def check(w) -> dict:
    """→ {"terms_strong": [...], "terms_weak": {术语: 次数}, "residue": [...], "missing": [...], "warnings": [...]}"""
    m = w.manifest
    segs = w.segs()
    terms = C.load_glossary_tsv(w.d / "glossary.tsv")
    matcher = C.TermMatcher(terms)
    tgt_cjk = C.is_cjk_lang(m["tgt_lang"])
    res = {"terms_strong": [], "terms_weak": {}, "term_hits": 0, "residue": [], "missing": [], "warnings": [],
           "chunks_done": 0, "chunks_total": len(w.chunks())}
    for c in w.chunks():
        r = C.read_json(w.chunk_file(c["id"]))
        if not r or r.get("trans") is None:
            continue
        res["chunks_done"] += 1
        res["warnings"] += [{"chunk": c["id"], "msg": x} for x in r.get("warnings", [])]
        for i in c["segs"]:
            s = segs[i]
            if not C.translatable(s):
                continue
            t = r["trans"].get(str(i), "")
            if not t.strip():
                res["missing"].append({"chunk": c["id"], "seg": i, "src": s["text"][:120]})
                continue
            for term in matcher.hits(s["text"]):
                res["term_hits"] += 1
                if term["dst"] in t:
                    continue
                other = [x for x in _alts(term.get("note", "")) if x != term["dst"] and x in t]
                if other:
                    res["terms_strong"].append({"chunk": c["id"], "seg": i, "src": term["src"], "want": term["dst"],
                                                "got": other[0]})
                else:
                    res["terms_weak"][term["src"]] = res["terms_weak"].get(term["src"], 0) + 1
            if tgt_cjk and not C.is_cjk_lang(m["src_lang"]):
                for mm in LATIN_RUN.finditer(C.PH_RE.sub("", t)):
                    res["residue"].append({"chunk": c["id"], "seg": i, "text": mm.group(0)[:120]})
    return res


def mark_term_redo(w) -> int:
    res = check(w)
    by_chunk: dict[int, set] = {}
    for x in res["terms_strong"]:
        by_chunk.setdefault(x["chunk"], set()).add(f"{x['src']} 必须译为「{x['want']}」(不要用「{x['got']}」)")
    for x in res["residue"] + res["missing"]:
        by_chunk.setdefault(x["chunk"], set()).add("上次有段落漏译或残留原文, 每段都要完整翻译")
    for cid, fbs in by_chunk.items():
        rec = C.read_json(w.chunk_file(cid)) or {}
        rec["redo"] = True
        rec["redo_feedback"] = "; ".join(sorted(fbs))
        C.write_json(w.chunk_file(cid), rec)
    return len(by_chunk)


def cmd_check(a, w):
    from long_translate import cmd_translate, log
    res = check(w)
    C.write_json(w.d / "check.json", res)
    weak_total = sum(res["terms_weak"].values())
    log(f"check: 已完成 {res['chunks_done']}/{res['chunks_total']} 块 | 术语命中 {res['term_hits']} 处, "
        f"明确不一致 {len(res['terms_strong'])} 处, 未出现标准译名 {weak_total} 处 (含代词指代, 仅供参考) | "
        f"漏译段 {len(res['missing'])} | 原文残留 {len(res['residue'])} | 告警 {len(res['warnings'])}")
    for x in res["terms_strong"][:10]:
        log(f"  术语: 块{x['chunk']} 段{x['seg']} {x['src']} 应为「{x['want']}」实为「{x['got']}」")
    for x in res["residue"][:5]:
        log(f"  残留: 块{x['chunk']} 段{x['seg']} {x['text']}")
    if a.fix_terms:
        n = mark_term_redo(w)
        log(f"check: 标记 {n} 块重翻")
        if n:
            a.limit, a.redo = 0, ""
            cmd_translate(a, w)
    return res


# ── 抽检 ───────────────────────────────────────────────────────
REVIEW_SYS = ("你是翻译审校。对照原文和译文 (按 ⟪n⟫ 段对齐), 只输出 JSON:\n"
              '{"score": 1-5 的整数 (5=出版级, 4=小瑕疵, 3=有明显误译/漏译, 2=多处错误, 1=不可用), '
              '"issues": [{"seg": 段号, "type": "漏译|误译|术语|不通顺|格式", "detail": "简述, 引用原文与译文"}]}\n'
              "只列真实问题, 没有就给空数组。")


def _pairs(w, c, rec, key="trans"):
    segs = w.segs()
    ids = [i for i in c["segs"] if C.translatable(segs[i])]
    src = "\n\n".join(f"⟪{k + 1}⟫\n{segs[i]['text']}" for k, i in enumerate(ids))
    tgt = "\n\n".join(f"⟪{k + 1}⟫\n{rec[key].get(str(i), '')}" for k, i in enumerate(ids))
    return ids, src, tgt


def cmd_review(a, w):
    from long_translate import log, parse_json_loose, run_pool
    done = [c for c in w.chunks() if w.done(c["id"])]
    random.Random(a.seed).shuffle(done)
    pick = done[:a.sample]
    llm = LLM(load_model(a.config, a.model), thinking=a.thinking)
    usage, results = Usage(), []

    def one(c):
        rec = w.done(c["id"])
        _, src, tgt = _pairs(w, c, rec)
        content, _, u = llm.chat([{"role": "system", "content": REVIEW_SYS},
                                  {"role": "user", "content": f"【原文】\n{src}\n\n【译文】\n{tgt}"}], max_tokens=4096)
        usage.add(u)
        j = parse_json_loose(content) or {}
        results.append({"chunk": c["id"], "score": j.get("score"), "issues": j.get("issues") or []})
    t0 = time.time()
    run_pool(pick, one, a.jobs, "抽检", t0, usage)
    llm.close()
    results.sort(key=lambda x: x["chunk"])
    scores = [x["score"] for x in results if isinstance(x["score"], (int, float))]
    out = {"sample": len(results), "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
           "results": results, "usage": usage.as_dict(), "ts": time.strftime("%F %T")}
    C.write_json(w.d / "review.json", out)
    from long_translate import add_stats
    add_stats(w, "review", usage, time.time() - t0)
    log(f"review: 抽检 {len(results)} 块, 平均分 {out['avg_score']}, 问题 {sum(len(x['issues']) for x in results)} 条 → review.json")
    return out


# ── 润色 ───────────────────────────────────────────────────────
def polish_sys(m):
    return (f"你是{C.lang_name(m['tgt_lang'])}出版社的资深文字编辑。给你原文和初译 (按 ⟪n⟫ 段对齐), "
            "请在忠实原文的前提下润色初译: 修正误译漏译、消除翻译腔、统一称谓, 不要删减内容。"
            "输出润色后的完整译文, 保留全部 ⟪n⟫ 标记 (数量顺序与输入一致) 和 ⟨n⟩ 占位符, 不要任何解释。"
            + (f" 风格要求: {m['style']}" if m.get("style") else ""))


def cmd_polish(a, w):
    from long_translate import add_stats, log, run_pool
    m = w.manifest
    terms = C.TermMatcher(C.load_glossary_tsv(w.d / "glossary.tsv"))
    todo = [c for c in w.chunks() if (r := w.done(c["id"])) and (not r.get("polished") or r.get("polished_stale"))]
    if a.limit:
        todo = todo[:a.limit]
    llm = LLM(load_model(a.config, a.model), thinking=a.thinking)
    usage = Usage()
    log(f"polish: 待润色 {len(todo)} 块, 并发 {a.jobs}")

    def one(c):
        rec = w.done(c["id"])
        ids, src, tgt = _pairs(w, c, rec)
        hit = terms.hits(src)
        gl = ("【术语表】\n" + "\n".join(f"- {t['src']} → {t['dst']}" for t in hit) + "\n\n") if hit else ""
        content, fin, u = llm.chat([{"role": "system", "content": polish_sys(m)},
                                    {"role": "user", "content": f"{gl}【原文】\n{src}\n\n【初译】\n{tgt}"}],
                                   max_tokens=llm.m["max_tokens"])
        usage.add(u)
        got = C.parse_marked(content, len(ids))
        pol, kept = {}, 0
        for k, i in enumerate(ids):
            draft = rec["trans"].get(str(i), "")
            new = got.get(k + 1, "")
            if fin == "length" or not new.strip() or len(new) < 0.75 * len(draft):
                pol[str(i)] = draft                    # 润色结果可疑 → 保留初译
                kept += 1
            else:
                pol[str(i)] = new
        rec["polished"] = pol
        rec.pop("polished_stale", None)
        if kept:
            rec.setdefault("warnings", []).append(f"润色: {kept} 段结果可疑, 保留初译")
        C.write_json(w.chunk_file(c["id"]), rec)
    t0 = time.time()
    run_pool(todo, one, a.jobs, "润色", t0, usage)
    llm.close()
    add_stats(w, "polish", usage, time.time() - t0)


# ── 质检报告 ───────────────────────────────────────────────────
def report(w, merged: dict, missing_chunks: list, out_path: Path) -> Path:
    m = w.manifest
    res = check(w)
    st = C.read_json(w.d / "stats.json", {})
    rv = C.read_json(w.d / "review.json")
    tgt_text = "\n".join(merged.values())
    tok_in = sum(p.get("prompt", 0) for p in st.values())
    tok_out = sum(p.get("completion", 0) for p in st.values())
    secs_tr = st.get("translate", {}).get("secs", 0)
    L = [f"# 翻译质检报告: {Path(m['src']).name}", "",
         f"- 输出: `{out_path}`",
         f"- 语言: {m['src_lang']} → {m['tgt_lang']} | 模型: {m['model']} | 格式: {m['fmt']}",
         f"- 原文: 约 {m['words']:,} 字/词, {m['chars']:,} 字符, {m['n_segs']} 段, {m['n_chunks']} 块 (每块≈{m['chunk_tokens']} token)",
         f"- 译文: {C.word_count(tgt_text):,} 字, {len(tgt_text):,} 字符",
         f"- 完成: {res['chunks_done']}/{res['chunks_total']} 块" + (f", **缺 {len(missing_chunks)} 块 (输出里用原文占位): {missing_chunks[:20]}**" if missing_chunks else ""),
         f"- 耗时: 翻译 {C.fmt_secs(secs_tr)}, 全部阶段 {C.fmt_secs(sum(p.get('secs', 0) for p in st.values()))}",
         f"- token: 输入 {tok_in:,} / 输出 {tok_out:,} (其中思考 {sum(p.get('reasoning', 0) for p in st.values()):,}), 调用 {sum(p.get('calls', 0) for p in st.values())} 次",
         "", "| 阶段 | 调用 | 输入 token | 输出 token | 用时 |", "|---|---|---|---|---|"]
    for k, p in st.items():
        L.append(f"| {k} | {p['calls']} | {p['prompt']:,} | {p['completion']:,} | {C.fmt_secs(p['secs'])} |")
    L += ["", "## 检查结果", "",
          f"- 漏译段 (无译文): {len(res['missing'])}",
          f"- 原文残留 (译文里连续 7+ 个英文词): {len(res['residue'])}",
          f"- 术语: 命中 {res['term_hits']} 处; 明确用了别的译名 {len(res['terms_strong'])} 处; "
          f"未出现标准译名 {sum(res['terms_weak'].values())} 处 (多为代词/省略, 参考用)",
          f"- 翻译告警 (过短/截断/重试后漏段): {len(res['warnings'])}"]
    if rv:
        L.append(f"- 抽检: {rv['sample']} 块, 平均 {rv['avg_score']} 分 (5 分制)")
    for title, rows, fmt in [
        ("漏译段", res["missing"], lambda x: f"- 块{x['chunk']} 段{x['seg']}: {x['src']}"),
        ("术语不一致", res["terms_strong"], lambda x: f"- 块{x['chunk']} 段{x['seg']}: {x['src']} 应为「{x['want']}」实为「{x['got']}」"),
        ("原文残留", res["residue"], lambda x: f"- 块{x['chunk']} 段{x['seg']}: {x['text']}"),
        ("翻译告警", res["warnings"], lambda x: f"- 块{x['chunk']}: {x['msg']}")]:
        if rows:
            L += ["", f"### {title} ({len(rows)})"] + [fmt(x) for x in rows[:50]]
            if len(rows) > 50:
                L.append(f"- … 其余 {len(rows) - 50} 条见 qa_report.json")
    weak = sorted(res["terms_weak"].items(), key=lambda kv: -kv[1])[:20]
    if weak:
        L += ["", "### 术语未出现标准译名最多的 (抽查用)"] + [f"- {k}: {v} 处" for k, v in weak]
    if rv:
        L += ["", "### 抽检问题"]
        for x in rv["results"]:
            for iss in x["issues"][:5]:
                L.append(f"- 块{x['chunk']} ({x['score']}分) [{iss.get('type', '')}] {iss.get('detail', '')}")
    L += ["", "处理建议: 术语不一致/残留 → `check --fix-terms`; 个别块 → `translate --redo 块号`; 改完 `build`。"]
    p = w.d / "qa_report.md"
    C.atomic_write(p, "\n".join(L) + "\n")
    C.write_json(w.d / "qa_report.json", {"check": res, "stats": st, "review": rv, "missing_chunks": missing_chunks})
    return p
