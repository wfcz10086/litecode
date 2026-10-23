#!/usr/bin/env python3
"""long-translate: 百万字级长文翻译 (切块 → 术语表 → 并发翻译+断点续传 → 一致性检查 → 原格式输出 + 质检报告)。

  python3 long_translate.py run  书.epub -w 工作目录 --to zh -j 4      # 一条龙, 可重复执行 = 续跑
  python3 long_translate.py init 书.txt -w W --to zh                  # 只抽段+切块
  python3 long_translate.py glossary W -j 4                           # 全书扫描 → W/glossary.tsv (可人工改)
  python3 long_translate.py translate W -j 4                          # 翻译未完成的块 (断点续传)
  python3 long_translate.py check W [--fix-terms]                     # 术语/漏译检查, --fix-terms 重翻问题块
  python3 long_translate.py review W --sample 5                       # 抽检打分 (可选)
  python3 long_translate.py polish W -j 4                             # 第二遍润色 (可选)
  python3 long_translate.py build W                                   # 输出原格式译文 + qa_report.md
  python3 long_translate.py status W                                  # 看进度 (后台跑时用)
"""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import signal
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lt_core as C  # noqa: E402
import lt_formats as F  # noqa: E402
from lt_llm import LLM, LLMError, Usage, load_model  # noqa: E402

STOP = threading.Event()


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# ── 工作目录 ───────────────────────────────────────────────────
class Work:
    def __init__(self, wd: str | Path):
        self.d = Path(wd).resolve()
        self.out = self.d / "out"
        self.scan = self.d / "scan"

    @property
    def manifest(self) -> dict:
        m = C.read_json(self.d / "manifest.json")
        if not m:
            raise SystemExit(f"{self.d} 不是 long-translate 工作目录 (先跑 init / run)")
        return m

    def segs(self) -> list[dict]:
        return C.read_jsonl(self.d / "segments.jsonl")

    def chunks(self) -> list[dict]:
        return C.read_json(self.d / "chunks.json", [])

    def chunk_file(self, cid: int) -> Path:
        return self.out / f"chunk_{cid:05d}.json"

    def done(self, cid: int) -> dict | None:
        r = C.read_json(self.chunk_file(cid))
        return r if r and r.get("trans") is not None and not r.get("redo") else None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cmd_init(a) -> Work:
    src = Path(a.src).resolve()
    if not src.is_file():
        raise SystemExit(f"文件不存在: {src}")
    w = Work(a.workdir or src.with_suffix("").as_posix() + f".lt_{a.to}")
    old = C.read_json(w.d / "manifest.json")
    digest = sha256(src)
    if old:
        if old["src_sha"] != digest and not a.reset:
            raise SystemExit("工作目录里是另一个版本的原文 (sha 不同)。换个 -w, 或加 --reset 清空重来")
        if old["src_sha"] == digest and not a.reset:
            log(f"工作目录已存在, 沿用: {w.d} ({old['n_chunks']} 块)")
            return w
    model = load_model(a.config, a.model)
    fmt, segs = F.extract(src, None if a.src_lang == "auto" else C.is_cjk_lang(a.src_lang))
    full = "\n".join(s["text"] for s in segs if C.translatable(s))
    src_lang = C.detect_lang(full) if a.src_lang == "auto" else a.src_lang
    # 块大小: 默认 1800 token; 不超过 上下文/6 和 输出上限/3 (译文膨胀 + 思考 token 都占 max_tokens)
    ct = a.chunk_tokens or 1800
    ct = max(300, min(ct, model["context_window"] // 6, model["max_tokens"] // 3))
    segs = C.explode_long_segments(segs, int(ct * 0.9))
    chunks = C.make_chunks(segs, ct)
    w.d.mkdir(parents=True, exist_ok=True)
    if a.reset:
        for p in list(w.out.glob("*.json")) + list(w.scan.glob("*.json")):
            p.unlink()
    C.write_jsonl(w.d / "segments.jsonl", segs)
    C.write_json(w.d / "chunks.json", chunks)
    C.write_json(w.d / "manifest.json", {
        "src": str(src), "src_sha": digest, "fmt": fmt, "src_lang": src_lang, "tgt_lang": a.to,
        "style": a.style or "", "chunk_tokens": ct, "model": model["id"], "created": time.strftime("%F %T"),
        "n_segs": len(segs), "n_chunks": len(chunks), "words": C.word_count(full), "chars": len(full),
        "src_tokens_est": sum(c["tokens"] for c in chunks)})
    log(f"init: {src.name} 格式={fmt} 语言={src_lang}→{a.to} 字数≈{C.word_count(full)} "
        f"段={len(segs)} 块={len(chunks)} (每块≈{ct} token) → {w.d}")
    return w


# ── 全书扫描: 术语 + 分段摘要 ──────────────────────────────────
SCAN_SYS = ("你是翻译项目的术语专员。阅读给出的原文片段, 输出 JSON (不要别的文字):\n"
            '{"summary": "用{tgt}写 2~3 句本片段情节/内容摘要 (含人物关系)", '
            '"terms": [{"src": "原文写法", "dst": "{tgt}译名", "type": "person|place|org|term|title"}]}\n'
            "terms 只收需要全书统一译法的: 人名、地名、机构、专有名词、领域术语、称谓头衔; "
            "不要收普通词汇。人名按通行译法 (如有经典译名用经典译名)。src 必须与原文写法完全一致。")


def scan_windows(segs: list[dict], win_tokens: int) -> list[list[int]]:
    wins, cur, tok = [], [], 0
    for s in segs:
        if not C.translatable(s):
            continue
        t = C.est_tokens(s["text"])
        if cur and tok + t > win_tokens:
            wins.append(cur)
            cur, tok = [], 0
        cur.append(s["i"])
        tok += t
    if cur:
        wins.append(cur)
    return wins


def cmd_glossary(a, w: Work | None = None):
    w = w or Work(a.workdir)
    m = w.manifest
    segs = w.segs()
    wins = scan_windows(segs, a.window_tokens)
    tgt = C.lang_name(m["tgt_lang"])
    llm = LLM(load_model(a.config, a.model or ""), thinking=a.thinking)
    usage = Usage()
    w.scan.mkdir(parents=True, exist_ok=True)
    todo = [k for k in range(len(wins)) if not (w.scan / f"win_{k:04d}.json").exists()]
    log(f"glossary: {len(wins)} 个扫描窗口 (每个≈{a.window_tokens} token), 待扫 {len(todo)}")
    t0 = time.time()

    def one(k):
        text = "\n\n".join(segs[i]["text"] for i in wins[k])
        content, _, u = llm.chat([{"role": "system", "content": SCAN_SYS.replace("{tgt}", tgt)},
                                  {"role": "user", "content": text}], max_tokens=4096)
        usage.add(u)
        j = parse_json_loose(content) or {}
        C.write_json(w.scan / f"win_{k:04d}.json", {"segs": [wins[k][0], wins[k][-1]],
                                                   "summary": str(j.get("summary") or "")[:600],
                                                   "terms": [t for t in (j.get("terms") or []) if isinstance(t, dict)],
                                                   "usage": u})
    run_pool(todo, one, a.jobs, "扫描", t0, usage)
    llm.close()
    # 合并
    cands, summaries = [], []
    for k in range(len(wins)):
        r = C.read_json(w.scan / f"win_{k:04d}.json")
        if r:
            cands += r["terms"]
            summaries.append({"segs": r["segs"], "summary": r["summary"]})
    full = "\n".join(s["text"] for s in segs if C.translatable(s))
    terms = C.harmonize_names(C.merge_term_candidates(cands, full, min_count=a.min_count), full)
    gpath = w.d / "glossary.tsv"
    if gpath.exists() and not a.overwrite:
        user = {t["src"]: t for t in C.load_glossary_tsv(gpath)}       # 保留用户改过的译名
        for t in terms:
            if t["src"] in user:
                t["dst"] = user[t["src"]]["dst"]
        known = {t["src"] for t in terms}
        terms += [t for s, t in user.items() if s not in known]
    C.save_glossary_tsv(gpath, terms)
    C.write_json(w.d / "summaries.json", summaries)
    add_stats(w, "glossary", usage, time.time() - t0)
    log(f"glossary: 术语 {len(terms)} 条 → {gpath} (有分歧的 {sum(1 for t in terms if t['note'])} 条, 见备注列)")


def parse_json_loose(text: str):
    import json
    import re
    m = re.search(r"```(?:json)?\s*(.+?)```", text or "", re.S)
    cand = m.group(1) if m else (text or "")
    i, j = cand.find("{"), cand.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(cand[i:j + 1])
        except ValueError:
            return None
    return None


# ── 并发执行 + 进度 ────────────────────────────────────────────
def run_pool(items: list, fn, jobs: int, label: str, t0: float, usage: Usage, weights: dict | None = None,
             total_weight: float = 0, done_weight: float = 0):
    """并发跑 fn(item); 每完成一个打印进度/ETA/token。weights 按原文 token 加权估算剩余时间。"""
    if not items:
        return []
    errors = []
    w_done, n_done = 0.0, 0
    total = len(items)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {}
        it = iter(items)
        for x in it:                                   # 先塞 jobs 个, 完成一个补一个 (方便 STOP 时少浪费)
            futs[ex.submit(fn, x)] = x
            if len(futs) >= jobs:
                break
        while futs:
            for f in as_completed(list(futs)):
                x = futs.pop(f)
                try:
                    f.result()
                    n_done += 1
                    w_done += weights.get(x, 1) if weights else 1
                except Exception as e:                  # noqa: BLE001 - 单块失败不拖垮全局
                    errors.append((x, str(e)[:300]))
                    log(f"  ✗ {label} {x.get('id', '') if isinstance(x, dict) else x} 失败: {str(e)[:200]}")
                el = time.time() - t0
                if weights:
                    rem = max(0.0, total_weight - done_weight - w_done)
                    eta = el / w_done * rem if w_done else 0
                    pct = (done_weight + w_done) / total_weight * 100 if total_weight else 100
                else:
                    eta = el / max(n_done, 1) * (total - n_done - len(errors))
                    pct = (n_done + len(errors)) / total * 100
                u = usage.as_dict()
                log(f"[{label}] {n_done + len(errors)}/{total} 本次 | 总进度 {pct:.1f}% | 用时 {C.fmt_secs(el)} | "
                    f"剩余≈{C.fmt_secs(eta)} | token 入{u['prompt']:,} 出{u['completion']:,} (思考{u['reasoning']:,}) | 失败 {len(errors)}")
                if not STOP.is_set():
                    nxt = next(it, None)
                    if nxt is not None:
                        futs[ex.submit(fn, nxt)] = nxt
                break
    if STOP.is_set():
        log("收到停止信号, 已完成的块都已落盘, 重跑同一命令即可续跑")
    return errors


def add_stats(w: Work, phase: str, usage: Usage, secs: float):
    st = C.read_json(w.d / "stats.json", {})
    p = st.setdefault(phase, {"prompt": 0, "completion": 0, "reasoning": 0, "calls": 0, "secs": 0.0})
    for k, v in usage.as_dict().items():
        p[k] += v
    p["secs"] = round(p["secs"] + secs, 1)
    C.write_json(w.d / "stats.json", st)


# ── 翻译 ───────────────────────────────────────────────────────
class Translator:
    def __init__(self, w: Work, a):
        self.w, self.a = w, a
        self.m = w.manifest
        self.segs = w.segs()
        self.chunks = w.chunks()
        self.terms = C.load_glossary_tsv(w.d / "glossary.tsv")
        self.matcher = C.TermMatcher(self.terms)
        self.summaries = C.read_json(w.d / "summaries.json", [])
        self.llm = LLM(load_model(a.config, a.model or ""), thinking=a.thinking, temperature=a.temperature)
        self.usage = Usage()
        self.ratios: list[float] = []
        self.lock = threading.Lock()
        self.tl = threading.local()
        self.tick = time.time()
        self.src_lang, self.tgt_lang = self.m["src_lang"], self.m["tgt_lang"]
        self.sys = C.system_prompt(self.src_lang, self.tgt_lang, self.m.get("style", ""))
        for c in self.chunks:                           # 用已完成块初始化中位长度比
            r = w.done(c["id"])
            if r and r.get("ratio"):
                self.ratios.append(r["ratio"])

    def median(self):
        with self.lock:
            return statistics.median(self.ratios) if len(self.ratios) >= 5 else None

    def summary_for(self, first_seg: int) -> str:
        prev = [s for s in self.summaries if s["segs"][0] <= first_seg]
        return "\n".join(s["summary"] for s in prev[-2:] if s["summary"])

    def context(self, c: dict) -> tuple[str, str]:
        if c["id"] == 0:
            return "", ""
        pc = self.chunks[c["id"] - 1]
        src_tail = " ".join(self.segs[i]["text"] for i in pc["segs"][-3:] if C.translatable(self.segs[i]))
        prev = self.w.done(pc["id"])
        tgt_tail = ""
        if prev:
            tgt_tail = "\n".join(prev["trans"].get(str(i), "") for i in pc["segs"][-3:])
        n = 400 if not C.is_cjk_lang(self.src_lang) else 200
        return C.tail_sentences(src_tail, n), C.tail_sentences(tgt_tail, 200 if C.is_cjk_lang(self.tgt_lang) else 400)

    def call(self, ids: list[int], c: dict, feedback: str = "") -> tuple[dict, list, list, bool]:
        texts = [self.segs[i]["text"] for i in ids]
        terms = self.matcher.hits("\n".join(texts))
        first = next(i for i in c["segs"] if C.translatable(self.segs[i]))
        if ids[0] == first:
            ps, pt = self.context(c)
        else:                                           # 拆半重翻的后半: 用块内前文作上下文
            before = [self.segs[i]["text"] for i in c["segs"] if i < ids[0] and C.translatable(self.segs[i])]
            ps, pt = C.tail_sentences(" ".join(before[-3:]), 400), ""
        user = C.build_user_prompt(texts, terms, self.summary_for(ids[0]), ps, pt, feedback)
        content, fin, u = self.llm.chat([{"role": "system", "content": self.sys}, {"role": "user", "content": user}],
                                        max_tokens=self.llm.m["max_tokens"])
        self.usage.add(u)
        getattr(self.tl, "usage", Usage()).add(u)         # 本块 (本线程) 用量
        got = C.parse_marked(content, len(ids))
        trans = {i: got[k + 1] for k, i in enumerate(ids) if k + 1 in got}
        missing = [i for i in ids if not trans.get(i, "").strip()]
        med = self.median()
        short = [i for i in ids if i in trans and C.too_short(self.segs[i]["text"], trans[i], self.src_lang,
                                                             self.tgt_lang, med)]
        return trans, missing, short, fin == "length"

    def translate_ids(self, ids: list[int], c: dict, depth: int = 0) -> tuple[dict, list[str]]:
        """翻译一组段; 漏段/截断/过短 → 带反馈重翻; 两次不行就对半拆开递归。"""
        best, warns, fb = {}, [], ""
        for attempt in range(2):
            trans, missing, short, trunc = self.call(ids, c, fb)
            if len(trans) - len(missing) >= len(best):
                best = trans
            if not missing and not short and not trunc:
                return trans, warns
            local = {i: k + 1 for k, i in enumerate(ids)}
            log(f"  ~ 块{c['id']} 第{attempt + 1}次: {len(ids)} 段, 漏 {[local[i] for i in missing]} "
                f"过短 {[local[i] for i in short]} 截断 {trunc}")
            fb = ("上次输出" + ("被截断了; " if trunc else "")
                  + (f"漏了第 {', '.join(str(local[i]) for i in missing)} 段; " if missing else "")
                  + (f"第 {', '.join(str(local[i]) for i in short)} 段译文明显过短, 疑似漏译; " if short else "")
                  + "这次务必完整翻译每一段, 保留全部 ⟪n⟫ 标记。")
        if len(ids) > 1 and depth < 4:
            mid = len(ids) // 2
            t1, w1 = self.translate_ids(ids[:mid], c, depth + 1)
            t2, w2 = self.translate_ids(ids[mid:], c, depth + 1)
            return {**t1, **t2}, warns + w1 + w2
        i = ids[0]
        if missing or not best.get(i):
            warns.append(f"段{i}: 重试后仍无译文 (漏译)")
        elif short:
            warns.append(f"段{i}: 译文过短 (比值 {C.length_ratio(self.segs[i]['text'], best[i]):.2f}), 请人工核对")
        elif trunc:
            warns.append(f"段{i}: 输出被截断")
        return best, warns

    def do_chunk(self, cid: int):
        if STOP.is_set():
            return
        c = self.chunks[cid]
        t0 = time.time()
        self.tl.usage = Usage()
        ids = [i for i in c["segs"] if C.translatable(self.segs[i])]
        old = C.read_json(self.w.chunk_file(cid)) or {}
        fb_extra = old.get("redo_feedback", "")
        trans, warns = (self.translate_ids(ids, c) if not fb_extra else self.translate_with_feedback(ids, c, fb_extra))
        src_len = sum(len(self.segs[i]["text"]) for i in ids)
        ratio = sum(len(trans.get(i, "")) for i in ids) / max(1, src_len)
        rec = {"id": cid, "trans": {str(i): t for i, t in trans.items()}, "warnings": warns, "ratio": round(ratio, 4),
               "usage": self.tl.usage.as_dict(), "secs": round(time.time() - t0, 1),
               "model": self.llm.m["id"], "ts": time.strftime("%F %T"),
               "terms": [t["src"] for t in self.matcher.hits("\n".join(self.segs[i]["text"] for i in ids))]}
        if old.get("polished"):
            rec["polished_stale"] = True
        C.write_json(self.w.chunk_file(cid), rec)
        with self.lock:
            self.ratios.append(ratio)
            now = time.time()                            # 逐块记账: 被 kill -9 也不丢已完成部分的用量/用时
            add_stats(self.w, "translate", self.tl.usage, now - self.tick)
            self.tick = now
        if warns:
            log(f"  ! 块 {cid}: " + "; ".join(warns[:3]))

    def translate_with_feedback(self, ids, c, fb):
        trans, missing, short, trunc = self.call(ids, c, fb)
        if missing or short or trunc:
            return self.translate_ids(ids, c)
        return trans, []


def cmd_translate(a, w: Work | None = None):
    w = w or Work(a.workdir)
    tr = Translator(w, a)
    chunks = tr.chunks
    if a.redo:
        for cid in parse_ids(a.redo):
            rec = C.read_json(w.chunk_file(cid)) or {}
            rec["redo"] = True
            C.write_json(w.chunk_file(cid), rec)
    todo = [c["id"] for c in chunks if not w.done(c["id"])]
    if a.limit:
        todo = todo[:a.limit]
    total_w = sum(c["tokens"] for c in chunks)
    done_w = total_w - sum(chunks[i]["tokens"] for i in todo)
    log(f"translate: 共 {len(chunks)} 块, 已完成 {len(chunks) - len(todo) if not a.limit else '…'}, 本次 {len(todo)} 块, "
        f"并发 {a.jobs}, 模型 {tr.llm.m['id']}, 术语 {len(tr.terms)} 条")
    if not todo:
        log("translate: 全部完成, 无需翻译")
        return []
    (w.d / "run.pid").write_text(str(os.getpid()))
    t0 = tr.tick = time.time()
    weights = {c["id"]: c["tokens"] for c in chunks}
    errs = run_pool(todo, tr.do_chunk, a.jobs, "翻译", t0, tr.usage, weights, total_w, done_w)
    tr.llm.close()
    left = sum(1 for c in chunks if not w.done(c["id"]))
    log(f"translate: 本次结束, 用时 {C.fmt_secs(time.time() - t0)}, 剩余未完成 {left} 块"
        + (f", 失败 {len(errs)} 块 (重跑即可重试)" if errs else ""))
    return errs


def parse_ids(s: str) -> list[int]:
    out = []
    for part in str(s).split(","):
        part = part.strip()
        if "-" in part:
            x, y = part.split("-")
            out += list(range(int(x), int(y) + 1))
        elif part:
            out.append(int(part))
    return out


# ── 输出 ───────────────────────────────────────────────────────
def collect_final(w: Work, use_polish: bool = True) -> tuple[dict, dict, list[int]]:
    """→ (最终译文 {head段号: 原格式文本}, 原始译文 {段号: 译文}, 缺失块列表)。"""
    m, segs = w.manifest, w.segs()
    raw, missing = {}, []
    for c in w.chunks():
        r = w.done(c["id"])
        if not r:
            missing.append(c["id"])
            continue
        src = r.get("polished") if use_polish and r.get("polished") and not r.get("polished_stale") else r["trans"]
        raw.update({int(k): v for k, v in src.items()})
    merged = F.merge_continuations(segs, raw, C.is_cjk_lang(m["tgt_lang"]))
    final = {}
    for i, t in merged.items():
        s = segs[i]
        if m["fmt"] == "epub":
            t = html.escape(t, quote=False)
        final[i], _ = C.restore_placeholders(t, s.get("ph"))
    return final, merged, missing


def cmd_build(a, w: Work | None = None):
    import lt_qa
    w = w or Work(a.workdir)
    m = w.manifest
    final, merged, missing = collect_final(w, not getattr(a, "no_polish", False))
    src = Path(m["src"])
    out = Path(a.output) if getattr(a, "output", None) else w.d / (src.stem + f".{m['tgt_lang']}" + F.output_ext(m["fmt"]))
    F.write_output(m["fmt"], src, w.segs(), final, out, m["tgt_lang"])
    rep = lt_qa.report(w, merged, missing, out)
    log(f"build: 译文 → {out}" + (f" (缺 {len(missing)} 块, 用原文占位)" if missing else ""))
    log(f"build: 质检报告 → {rep}")
    return out


def cmd_status(a):
    w = Work(a.workdir)
    m = w.manifest
    chunks = w.chunks()
    done = [c for c in chunks if w.done(c["id"])]
    tok_all = sum(c["tokens"] for c in chunks) or 1
    tok_done = sum(c["tokens"] for c in done)
    st = C.read_json(w.d / "stats.json", {})
    tr = st.get("translate", {})
    pid = (w.d / "run.pid").read_text().strip() if (w.d / "run.pid").exists() else ""
    alive = bool(pid) and Path(f"/proc/{pid}").exists()
    warns = sum(len((C.read_json(w.chunk_file(c["id"])) or {}).get("warnings", [])) for c in done)
    print(f"原文: {m['src']}  ({m['fmt']}, {m['src_lang']}→{m['tgt_lang']}, 字数≈{m['words']:,}, 模型 {m['model']})")
    print(f"进度: {len(done)}/{len(chunks)} 块, 按原文量 {tok_done / tok_all * 100:.1f}%  | 进程: "
          + (f"运行中 pid={pid}" if alive else "未运行"))
    print(f"术语表: {'有' if (w.d / 'glossary.tsv').exists() else '无'} ({len(C.load_glossary_tsv(w.d / 'glossary.tsv'))} 条)"
          f" | 告警段: {warns}")
    secs = sum(p.get("secs", 0) for p in st.values())
    print(f"累计: 翻译用时 {C.fmt_secs(tr.get('secs', 0))}, 全部阶段 {C.fmt_secs(secs)}; token 入 "
          f"{sum(p.get('prompt', 0) for p in st.values()):,} 出 {sum(p.get('completion', 0) for p in st.values()):,}")
    if tr.get("secs") and tok_done:
        print(f"按已完成速度, 剩余≈{C.fmt_secs(tr['secs'] / tok_done * (tok_all - tok_done))} (同并发)")
    log_p = w.d / "run.log"
    if log_p.exists():
        print("── run.log 最后 5 行 ──")
        print("\n".join(log_p.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]))


def cmd_run(a):
    w = cmd_init(a)
    (w.d / "run.pid").write_text(str(os.getpid()))
    if not a.no_glossary and not (w.d / "glossary.tsv").exists():
        cmd_glossary(a, w)
    elif not a.no_glossary and len(list(w.scan.glob("win_*.json"))) < len(scan_windows(w.segs(), a.window_tokens)):
        cmd_glossary(a, w)                              # 扫描中途被打断过 → 补扫
    if STOP.is_set():
        return
    errs = cmd_translate(a, w)
    if STOP.is_set() or a.limit:
        return
    import lt_qa
    if a.fix_terms:
        n = lt_qa.mark_term_redo(w)
        if n:
            log(f"check: {n} 块术语不一致, 重翻")
            cmd_translate(a, w)
    if errs:
        log("有失败块, 先不 build; 重跑 run 续翻")
        return
    cmd_build(a, w)


# ── CLI ────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="百万字级长文翻译 (断点续传)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, wd=True):
        if wd:
            p.add_argument("workdir")
        p.add_argument("--config", default="", help="config.json 路径 (默认自动找)")
        p.add_argument("--model", default="", help="config.models[] 里的模型 id (默认当前激活模型)")
        p.add_argument("-j", "--jobs", type=int, default=4, help="并发数 (默认 4)")
        p.add_argument("--thinking", choices=["low", "auto", "off"], default="low",
                       help="low=reasoning_effort=low (默认, 省 90%% 思考 token); auto=模型默认; off=enable_thinking=false")
        p.add_argument("--temperature", type=float, default=None)
        return p

    def init_args(p):
        p.add_argument("src")
        p.add_argument("-w", "--workdir", default="")
        p.add_argument("--to", default="zh", help="目标语言 zh/en/ja/... (默认 zh)")
        p.add_argument("--from", dest="src_lang", default="auto")
        p.add_argument("--chunk-tokens", type=int, default=0, help="每块原文 token (默认 1800)")
        p.add_argument("--style", default="", help="风格要求, 如 '古典小说腔' '技术文档, 术语保留英文括注'")
        p.add_argument("--reset", action="store_true", help="清空已有工作目录重来")

    p = sub.add_parser("init")
    init_args(p)
    p.add_argument("--config", default="")
    p.add_argument("--model", default="")
    g = common(sub.add_parser("glossary"))
    g.add_argument("--window-tokens", type=int, default=6000)
    g.add_argument("--min-count", type=int, default=2)
    g.add_argument("--overwrite", action="store_true", help="不保留 glossary.tsv 里的人工修改")
    t = common(sub.add_parser("translate"))
    t.add_argument("--limit", type=int, default=0, help="本次最多翻 N 块 (试跑)")
    t.add_argument("--redo", default="", help="强制重翻的块号, 如 3,7,10-12")
    ck = common(sub.add_parser("check"))
    ck.add_argument("--fix-terms", action="store_true", help="把术语不一致的块标记重翻并立即重翻")
    rv = common(sub.add_parser("review"))
    rv.add_argument("--sample", type=int, default=5)
    rv.add_argument("--seed", type=int, default=7)
    po = common(sub.add_parser("polish"))
    po.add_argument("--limit", type=int, default=0)
    b = sub.add_parser("build")
    b.add_argument("workdir")
    b.add_argument("-o", "--output", default="")
    b.add_argument("--no-polish", action="store_true")
    sub.add_parser("status").add_argument("workdir")
    r = common(sub.add_parser("run"), wd=False)
    init_args(r)
    r.add_argument("--no-glossary", action="store_true")
    r.add_argument("--window-tokens", type=int, default=6000)
    r.add_argument("--min-count", type=int, default=2)
    r.add_argument("--overwrite", action="store_true")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--redo", default="")
    r.add_argument("--fix-terms", action="store_true")
    a = ap.parse_args(argv)
    if getattr(a, "jobs", 1) > 8:
        log("并发上限 8 (别把模型占满)")
        a.jobs = 8

    def _stop(sig, _f):
        if STOP.is_set():
            raise SystemExit(130)
        STOP.set()
        log(f"收到信号 {sig}: 等进行中的块写完后退出 (再按一次立即退出)")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if a.cmd in ("check", "review", "polish"):
        import lt_qa
        return {"check": lt_qa.cmd_check, "review": lt_qa.cmd_review, "polish": lt_qa.cmd_polish}[a.cmd](a, Work(a.workdir))
    return {"init": cmd_init, "glossary": cmd_glossary, "translate": cmd_translate, "build": cmd_build,
            "status": cmd_status, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    try:
        main()
    except LLMError as e:
        log(f"模型调用失败: {e}")
        sys.exit(2)
