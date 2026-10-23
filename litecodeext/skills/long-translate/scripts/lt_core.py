"""long-translate 核心算法 (纯函数, 不联网, pytest 直接测):
token 估算 / 段落切块 / 术语表匹配与注入 / 提示词 / 译文解析 / 长度异常检测 / 落盘工具。

段 (segment) = 文档里一个可独立翻译的单元 (段落、标题、字幕条、表格单元格…), 由 lt_formats 抽取:
  {"i": 序号, "text": 原文(占位符已替换), "kind": "p|heading|code|skip", "cont": 是否接上一段(超长段拆开的后半),
   "ph": {占位符: 原文}, "loc": 格式相关定位信息}
块 (chunk) = 若干连续段, 一次 LLM 调用翻译。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")
LETTER_RE = re.compile(r"[^\W\d_]", re.U)
SEG_MARK = "⟪{}⟫"
SEG_MARK_RE = re.compile(r"⟪\s*(\d+)\s*⟫")
PH_RE = re.compile(r"⟨\d+⟩")

LANG_NAMES = {"zh": "简体中文", "zh-tw": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어",
              "fr": "Français", "de": "Deutsch", "es": "Español", "ru": "Русский", "pt": "Português"}
CJK_LANGS = {"zh", "zh-tw", "ja", "ko"}

CHAPTER_RE = re.compile(
    r"^\s*(?:(?:chapter|book|part|volume|prologue|epilogue|preface|introduction|appendix)\b"
    r"[\s\w.:,'\-—]{0,60}"
    r"|[IVXLC]+\.?\s*$"
    r"|第[一二三四五六七八九十百千零〇两\d]+[章节回卷部篇集幕].{0,40})$", re.I)


# ── token / 字数 ──────────────────────────────────────────────
def est_tokens(text: str) -> int:
    """粗估 token: 中日韩字符按 1 个/字, 其余按 3.6 字符/token (英文散文实测 3.8~4.2, 留余量)。"""
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    return cjk + int((len(text) - cjk) / 3.6) + 1


def word_count(text: str) -> int:
    """"字数": 中日韩按字, 其他按空格分词。"""
    cjk = len(CJK_RE.findall(text))
    return cjk + len(re.findall(r"[A-Za-z0-9À-ɏ'’\-]+", CJK_RE.sub(" ", text)))


def is_cjk_lang(lang: str) -> bool:
    return (lang or "").lower() in CJK_LANGS


def detect_lang(text: str) -> str:
    sample = text[:20000]
    letters = len(LETTER_RE.findall(sample)) or 1
    return "zh" if len(CJK_RE.findall(sample)) / letters > 0.3 else "en"


def translatable(seg: dict) -> bool:
    return seg.get("kind") not in ("code", "skip") and bool(LETTER_RE.search(PH_RE.sub("", seg["text"])))


# ── 超长段按句拆 ───────────────────────────────────────────────
_SENT_END = re.compile(r"(?<=[.!?。！？;；…])[\"'”’）)]*\s+|(?<=[。！？；…])")


def split_long_text(text: str, max_tokens: int) -> list[str]:
    """单段超过 max_tokens 时按句子边界拆成多份; 找不到句界就硬切。"""
    if est_tokens(text) <= max_tokens:
        return [text]
    parts, cur = [], ""
    cuts = [m.end() for m in _SENT_END.finditer(text)]     # 按位置切, 保留标点和空白
    pieces, start = [], 0
    for c in cuts + [len(text)]:
        if c > start:
            pieces.append(text[start:c])
            start = c
    for p in pieces:
        if cur and est_tokens(cur + p) > max_tokens:
            parts.append(cur)
            cur = ""
        while est_tokens(p) > max_tokens:              # 单句本身就超长: 按字符硬切
            n = max(200, int(len(p) * max_tokens / est_tokens(p)))
            parts.append(p[:n])
            p = p[n:]
        cur += p
    if cur:
        parts.append(cur)
    return parts


def explode_long_segments(segs: list[dict], max_tokens: int) -> list[dict]:
    """把超长段拆成 head + cont 段 (回写时 merge_continuations 再拼回去)。重新编号 i。"""
    out = []
    for s in segs:
        if s.get("kind") in ("code", "skip") or est_tokens(s["text"]) <= max_tokens:
            out.append(dict(s))
            continue
        pieces = split_long_text(s["text"], max_tokens)
        for k, part in enumerate(pieces):
            d = dict(s, text=part, cont=k > 0, pre=s.get("pre", "") if k == 0 else "",
                     post=s.get("post", "") if k == len(pieces) - 1 else "")
            if k > 0:
                d.pop("ph", None)            # 占位符表只挂在首段, 合并后统一还原
            out.append(d)
    for i, s in enumerate(out):
        s["i"] = i
    return out


# ── 切块 ──────────────────────────────────────────────────────
def make_chunks(segs: list[dict], target_tokens: int, min_ratio: float = 0.35) -> list[dict]:
    """按段边界切块: 累计到 target_tokens 截断; 遇到章节标题且当前块已达 min_ratio×目标时提前截断,
    让章节尽量从块首开始。不可翻译段 (代码/空) 跟随相邻块, 不占预算。"""
    chunks, cur, cur_tok, chapter = [], [], 0, ""

    def flush():
        nonlocal cur, cur_tok
        if cur:
            chunks.append({"id": len(chunks), "segs": cur, "tokens": cur_tok, "chapter": chapter})
        cur, cur_tok = [], 0

    for s in segs:
        t = est_tokens(s["text"]) if translatable(s) else 0
        if (s.get("kind") == "heading" or s.get("brk")) and not s.get("cont") and cur_tok >= target_tokens * min_ratio:
            flush()
        elif cur and t and cur_tok + t > target_tokens and not s.get("cont"):
            flush()
        elif cur and t and cur_tok + t > target_tokens * 1.3:   # cont 段也不能无限接
            flush()
        if s.get("kind") == "heading" and translatable(s):
            chapter = s["text"].strip()[:60]
        cur.append(s["i"])
        cur_tok += t
    flush()
    first = segs[chunks[-1]["segs"][0]] if chunks and chunks[-1]["segs"][0] < len(segs) else {}
    if len(chunks) >= 2 and chunks[-1]["tokens"] < target_tokens * 0.25 and first.get("kind") != "heading" \
            and not first.get("brk") \
            and chunks[-2]["tokens"] + chunks[-1]["tokens"] <= target_tokens * 1.3:   # 尾巴太小并入上一块
        last = chunks.pop()
        chunks[-1]["segs"] += last["segs"]
        chunks[-1]["tokens"] += last["tokens"]
    chunks = [c for c in chunks if c["tokens"] > 0]          # 纯代码/空块不用翻, 回写时保留原文
    for k, c in enumerate(chunks):
        c["id"] = k
    return chunks


# ── 术语表 ─────────────────────────────────────────────────────
def load_glossary_tsv(path: Path) -> list[dict]:
    """glossary.tsv: 原文<TAB>译文<TAB>类型<TAB>次数<TAB>备注; # 开头为注释; 译文留空 = 不强制。"""
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        src = cols[0].strip()
        dst = cols[1].strip() if len(cols) > 1 else ""
        if src and dst:
            out.append({"src": src, "dst": dst, "type": cols[2].strip() if len(cols) > 2 else "",
                        "count": int(cols[3]) if len(cols) > 3 and cols[3].strip().isdigit() else 0,
                        "note": cols[4].strip() if len(cols) > 4 else ""})
    return out


def save_glossary_tsv(path: Path, terms: list[dict]):
    lines = ["# 术语表: 原文\t译文\t类型\t出现次数\t备注 —— 可直接编辑译文列; 删行=不约束; 改完重跑 translate 即生效",
             "# (已翻完的块不会自动重翻; 要让改动覆盖旧译文, 跑 check --fix-terms 或 translate --redo-terms)"]
    for t in terms:
        lines.append("\t".join([t["src"], t.get("dst", ""), t.get("type", ""), str(t.get("count", 0)),
                                t.get("note", "").replace("\t", " ")]))
    atomic_write(path, "\n".join(lines) + "\n")


def _term_pattern(src: str) -> str:
    esc = re.escape(src)
    if CJK_RE.search(src):
        return esc
    return r"(?<![\w])" + esc + r"(?![\w])"


class TermMatcher:
    """一次性编译所有术语为一个正则 (长词优先), 在块原文里找命中。"""

    def __init__(self, terms: list[dict]):
        self.terms = {t["src"]: t for t in terms}
        srcs = sorted(self.terms, key=len, reverse=True)
        self.rx = re.compile("|".join(_term_pattern(s) for s in srcs)) if srcs else None

    def hits(self, text: str) -> list[dict]:
        if not self.rx:
            return []
        seen, out = set(), []
        for m in self.rx.finditer(text):
            k = m.group(0)
            if k not in seen and k in self.terms:
                seen.add(k)
                out.append(self.terms[k])
        return out

    def count_all(self, text: str) -> dict:
        cnt = {}
        if self.rx:
            for m in self.rx.finditer(text):
                cnt[m.group(0)] = cnt.get(m.group(0), 0) + 1
        return cnt


def merge_term_candidates(cands: list[dict], full_text: str, min_count: int = 2, limit: int = 3000) -> list[dict]:
    """合并多个扫描窗口抽出的术语: 同一原文取出现最多的译名, 记录分歧; 按全文真实出现次数过滤。"""
    agg: dict[str, dict] = {}
    for c in cands:
        src = str(c.get("src") or "").strip()
        dst = str(c.get("dst") or "").strip()
        if not src or not dst or len(src) > 60 or src.lower() == dst.lower():
            continue
        a = agg.setdefault(src, {"src": src, "votes": {}, "type": str(c.get("type") or "")})
        a["votes"][dst] = a["votes"].get(dst, 0) + 1
        if not a["type"] and c.get("type"):
            a["type"] = str(c["type"])
    counts = TermMatcher([{"src": s} for s in agg]).count_all(full_text) if agg else {}
    out = []
    for src, a in agg.items():
        n = counts.get(src, 0)
        if n < min_count and a["type"] not in ("person", "place", "人名", "地名"):
            continue
        if n == 0:
            continue
        votes = sorted(a["votes"].items(), key=lambda kv: -kv[1])
        note = "" if len(votes) == 1 else "候选: " + " / ".join(f"{d}×{k}" for d, k in votes[:4])
        out.append({"src": src, "dst": votes[0][0], "type": a["type"], "count": n, "note": note,
                    "votes": votes[0][1]})
    out.sort(key=lambda t: (-t["count"], t["src"]))
    return out[:limit]


HONORIFICS = {"mr": ["先生"], "mrs": ["太太", "夫人"], "miss": ["小姐"], "ms": ["女士"], "dr": ["医生", "博士"],
              "lady": ["夫人", "小姐"], "sir": ["爵士"], "lord": ["勋爵", "爵爷"], "colonel": ["上校"],
              "captain": ["上尉", "船长"], "professor": ["教授"], "uncle": ["舅舅", "叔叔", "伯父"], "aunt": ["姨妈", "姑妈", "舅妈"]}
_HON_RE = re.compile(r"^(%s)\.?\s+(.+)$" % "|".join(HONORIFICS), re.I)


def _name_parts(src: str, dst: str) -> list[tuple[str, str]]:
    """从一条人名术语推出 [(英文名部件, 中文译法)]: 'Mr. Bennet'→贝内特先生 ⇒ (Bennet, 贝内特);
    'Elizabeth Bennet'→伊丽莎白·班纳特 ⇒ (Elizabeth, 伊丽莎白), (Bennet, 班纳特)。推不出就返回空。"""
    m = _HON_RE.match(src)
    if m:
        name = m.group(2)
        for suf in HONORIFICS[m.group(1).lower()]:
            if dst.endswith(suf) and len(dst) > len(suf):
                core = dst[:-len(suf)]
                if " " in name:
                    return _name_parts(name, core)
                return [] if re.search(r"[·・•\s]", core) else [(name, core)]   # Sir William→威廉·卢卡斯爵士: 对不上
        return []
    words, pieces = src.split(), re.split(r"[·・•\s]", dst)
    if len(words) == 1 and len(pieces) == 1 and src[:1].isupper() and src.isalpha():
        return [(src, dst)]
    if len(words) >= 2 and len(words) == len(pieces) and all(w[:1].isupper() for w in words):
        return list(zip(words, pieces))
    return []


def harmonize_names(terms: list[dict], full_text: str) -> list[dict]:
    """人名部件统一: 同一个姓/名在不同术语里译法不同 (贝内特先生 vs 伊丽莎白·班纳特) 时投票取多数
    (权重 = 扫描时模型给出该译法的窗口数, 平票再看全文出现次数),
    改写少数派并在备注里写明; 单独出现的部件 (如 'the Bennets' 里的 Bennet) 补成术语。"""
    votes: dict[str, dict[str, int]] = {}
    for t in terms:
        if t.get("type") not in ("person", "人名", "") or not CJK_RE.search(t.get("dst", "")):
            continue
        for w, z in _name_parts(t["src"], t["dst"]):
            if z:
                votes.setdefault(w, {}).setdefault(z, 0)
                votes[w][z] += max(1, int(t.get("votes") or 1)) * 100000 + int(t.get("count") or 0)
    best = {w: max(v.items(), key=lambda kv: kv[1])[0] for w, v in votes.items()}
    for t in terms:
        for w, z in _name_parts(t["src"], t["dst"]):
            if z and best.get(w) and z != best[w] and z in t["dst"]:
                t["dst"] = t["dst"].replace(z, best[w])
                t["note"] = (t.get("note", "") + f" 已统一 {w}→{best[w]} (原 {z})").strip()
    have = {t["src"] for t in terms}
    extra = [{"src": w, "dst": z, "type": "person", "note": "由全名推出"} for w, z in best.items() if w not in have]
    if extra:
        cnt = TermMatcher(extra).count_all(full_text)
        low = TermMatcher([{"src": e["src"].lower()} for e in extra]).count_all(full_text)
        for e in extra:
            if cnt.get(e["src"], 0) >= 2 and low.get(e["src"].lower(), 0) * 3 < cnt[e["src"]]:   # Long/Young 这类普通词跳过
                e["count"] = cnt[e["src"]]
                terms.append(e)
    return terms


# ── 提示词 ─────────────────────────────────────────────────────
def lang_name(code: str) -> str:
    return LANG_NAMES.get((code or "").lower(), code)


def system_prompt(src_lang: str, tgt_lang: str, style: str = "") -> str:
    s = (f"你是资深出版级译者, 把{lang_name(src_lang)}文本翻译成{lang_name(tgt_lang)}。要求:\n"
         "1. 忠实完整: 逐段翻译, 不遗漏、不增删、不总结, 对话和语气照原文。\n"
         "2. 自然流畅: 按目标语言出版物习惯行文, 不要翻译腔。\n"
         "3. 格式: 输入每段以一行标记 ⟪n⟫ 开头。输出必须保留全部 ⟪n⟫ 标记, 顺序和数量与输入完全一致, "
         "每个标记后紧跟该段译文; 不得合并、拆分或跳过段落。\n"
         "4. 形如 ⟨0⟩ ⟨1⟩ 的占位符 (代码、链接、标签) 原样保留在译文对应位置, 不要翻译或改动。\n"
         "5. 术语表里的译名必须照用; 人名地名全书统一。\n"
         "6. 「上下文」部分只供参考, 不要翻译也不要输出。\n"
         "7. 只输出译文, 不要任何解释、注释、前言。")
    if style:
        s += f"\n8. 风格要求: {style}"
    return s


def build_user_prompt(seg_texts: list[str], terms: list[dict], summary: str = "",
                      prev_src: str = "", prev_tgt: str = "", feedback: str = "") -> str:
    parts = []
    if terms:
        parts.append("【术语表】(必须照用)\n" + "\n".join(f"- {t['src']} → {t['dst']}" for t in terms))
    if summary:
        parts.append("【上下文: 前情摘要】(仅供参考)\n" + summary.strip())
    if prev_src:
        parts.append("【上下文: 上一段原文末尾】(仅供参考)\n" + prev_src.strip())
    if prev_tgt:
        parts.append("【上下文: 上一段已有译文末尾】(仅供参考, 保持衔接与称谓一致)\n" + prev_tgt.strip())
    if feedback:
        parts.append("【注意】" + feedback)
    body = "\n\n".join(f"{SEG_MARK.format(k + 1)}\n{t}" for k, t in enumerate(seg_texts))
    parts.append(f"【待翻译】共 {len(seg_texts)} 段\n\n" + body)
    return "\n\n".join(parts)


def parse_marked(text: str, n: int) -> dict[int, str]:
    """解析 ⟪k⟫ 标记的译文 → {k(1-based): 译文}; 重复标记取第一次出现。"""
    out: dict[int, str] = {}
    ms = list(SEG_MARK_RE.finditer(text or ""))
    for a, m in enumerate(ms):
        k = int(m.group(1))
        end = ms[a + 1].start() if a + 1 < len(ms) else len(text)
        if 1 <= k <= n and k not in out:
            out[k] = text[m.end():end].strip("\n").strip()
    if not ms and n == 1 and (text or "").strip():      # 单段时模型偶尔省略标记
        out[1] = text.strip()
    return out


def tail_sentences(text: str, n_chars: int = 300) -> str:
    """取文本末尾约 n_chars 字符, 尽量从句首开始。"""
    t = (text or "").strip()
    if len(t) <= n_chars:
        return t
    cut = t[-n_chars:]
    m = re.search(r"(?<=[.!?。！？”\"])\s*", cut)
    return cut[m.end():] if m and m.end() < len(cut) - 20 else cut


# ── 质量: 长度比检测 ───────────────────────────────────────────
def length_ratio(src: str, tgt: str) -> float:
    s = len(PH_RE.sub("", src).strip()) or 1
    return len(PH_RE.sub("", tgt).strip()) / s


def expected_ratio(src_lang: str, tgt_lang: str) -> tuple[float, float]:
    """(典型字符比, 报警下限)。拉丁→中日韩约 0.3, 反向约 3, 同文字系约 1。"""
    a, b = is_cjk_lang(src_lang), is_cjk_lang(tgt_lang)
    if not a and b:
        return 0.30, 0.12
    if a and not b:
        return 3.0, 1.0
    return 1.0, 0.45


def too_short(src: str, tgt: str, src_lang: str, tgt_lang: str, median: float | None = None,
              min_src_chars: int = 40) -> bool:
    """译文明显短于原文 → 疑似漏译/截断。有已完成块的中位比值时用自适应阈值 (中位数的 45%)。"""
    if len(src.strip()) < min_src_chars:
        return not tgt.strip() and bool(LETTER_RE.search(src))
    typical, floor = expected_ratio(src_lang, tgt_lang)
    thr = max(floor, 0.45 * median) if median else floor
    return length_ratio(src, tgt) < thr


def restore_placeholders(text: str, ph: dict | None) -> tuple[str, list[str]]:
    """还原 ⟨n⟩ 占位符; 返回 (文本, 丢失的占位符列表)。丢失的追加到段尾, 保证链接/代码不丢。"""
    if not ph:
        return text, []
    lost = []
    for k, v in ph.items():
        if k in text:
            text = text.replace(k, v)
        else:
            lost.append(k)
            text = text + " " + v
    return text, lost


# ── 落盘 ──────────────────────────────────────────────────────
def atomic_write(path: Path, data: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path: Path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1))


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_jsonl(path: Path, rows: list[dict]):
    atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def fmt_secs(s: float) -> str:
    s = int(max(0, s))
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"
