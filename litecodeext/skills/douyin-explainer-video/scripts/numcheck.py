#!/usr/bin/env python3
"""画面数字 ↔ 旁白数字 对账 (事实闸的一部分; factcheck.py 与 run.py 的 fact 步骤都会调用)
用法 (项目目录): python3 numcheck.py [script.json]      有矛盾 → 非 0 退出

来历: 样片某场画面大字是「428 秒」, 同场旁白却说「要走约八分二十秒」(=500 秒, 真实约 499 秒),
      factcheck 只看旁白, 没拦住。

做法:
  1. 每场抽"画面上的数值": 按 engine.js 各版式真正渲染成文字的字段
     (hook.number/title/tape, headline.kicker/title/lines, stamps.title/stamps, counter.label/from/to/note,
      versus.left/right.name/value, quote.quote, image.caption, 场景 act 幕标);
     数字参数按 engine.js 的 num() 原样拼成 "prefix + 千分位整数 + suffix" 再解析。自定义版式扫 params 里的字符串。
  2. 抽"旁白里的数值"。解析: 阿拉伯数字 (小数/千分位/百分号/k·M·B)、中文数字 (八 / 二十 / 三百四十 / 一万二千 /
     一万二 / 两 / 零点五 / 半)、量级 (万/亿/千)、单位 (公里/米、元/块/美元、年/月/周/天/小时/分钟/秒、%、岁、倍 …)、
     速度 (30万公里/秒, 每秒三十万公里, 一秒钟能跑约三十万公里)、
     时长组合 (八分二十秒 / 1 小时 20 分 / 两分半 / 一个半小时) → 秒;
     约/左右 ±6%, 多/余/超过 = 下限, 不到/近 = 上限, 不带修饰的精确数 ±1.5% (容许四舍五入)。
  3. 同一场、同一量纲 (时长 / 长度 / 速度 / 金额(分币种) / 百分比 / 年份 …):
     画面数字跟本场旁白任何一个都对不上, 而本场旁白确实说了这类数字, 并且整片旁白和 facts 里也找不到它 → 矛盾 (硬错误)。
     (VS 卡片左边是上一场讲过的数, 本场旁白只念了右边 —— 有出处, 不算矛盾。)
     本场旁白没说这类数字、整片和 facts 也没有 → 只提示 "画面数字没出处"。
  4. 没带单位的大数字 (hook.number / counter.to 没 prefix/suffix): 本场旁白有数却全对不上, 又无出处 → 矛盾。
  5. 口语相对量: 三成 / 七成五 / 五成以上 / 一半 / 过半 → 百分比, 翻一番 ×2 / 翻两番 ×4 / 增长一倍 ×2, 打八折 = 80% (或降 20%)。
  6. 画面自相矛盾: 同场 counter.to / hook.number (带单位) ↔ 同场 note/caption/副标题里的同量纲数字对不上 → 矛盾。
豁免: 场景里写 "numcheck_ignore": ["画面上那段文字的片段", ...] —— 包含这些片段的画面字段不查。
"""
import copy
import json
import math
import re
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 数字
CN_DIG = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_SMALL = {"十": 10, "百": 100, "千": 1000}
CN_BIG = {"万": 10 ** 4, "亿": 10 ** 8}
MAG = {"千": 1e3, "万": 1e4, "亿": 1e8, "k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9}


def cn_number(s: str) -> float:
    """中文数字 → 数值: 八 / 二十 / 三百四十 / 一万二千 / 一万二 (=12000) / 两 / 零点五 / 半 / 一九二九 (逐位) / 三点五亿"""
    s = s.strip()
    if s == "半":
        return 0.5
    if "点" in s:
        ip, fp = s.split("点", 1)
        m = re.fullmatch(r"([零〇一二三四五六七八九]+)([十百千万亿]*)", fp)
        if not m:
            raise ValueError(s)
        v = (cn_number(ip) if ip else 0.0) + float("0." + "".join(str(CN_DIG[c]) for c in m.group(1)))
        for c in m.group(2):
            v *= CN_SMALL.get(c) or CN_BIG[c]
        return v
    if not s or any(c not in CN_DIG and c not in CN_SMALL and c not in CN_BIG for c in s):
        raise ValueError(s)
    if len(s) >= 2 and all(c in CN_DIG for c in s):            # 一九二九 / 二零二四: 逐位读
        return float("".join(str(CN_DIG[c]) for c in s))
    total, sec, cur = 0, 0, 0
    for i, c in enumerate(s):
        if c in CN_DIG:
            cur = CN_DIG[c]
            prev = s[i - 1] if i else ""
            if i == len(s) - 1 and (prev in CN_SMALL or prev in CN_BIG) and c not in "零〇":
                # 口语省略末位单位: 一万二 = 12000, 三千五 = 3500, 两百五 = 250 (二十五 不受影响: 十/10 = 1)
                cur *= (CN_SMALL.get(prev) or CN_BIG[prev]) // 10
        elif c in CN_BIG:
            total = (total + sec + cur) * CN_BIG[c] if c == "亿" else total + (sec + cur) * CN_BIG[c]
            sec = cur = 0
        else:
            sec += (cur or 1) * CN_SMALL[c]
            cur = 0
    return float(total + sec + cur)


def _num(tok: str) -> float:
    tok = tok.replace(",", "")
    return float(tok) if re.fullmatch(r"\d+(?:\.\d+)?", tok) else cn_number(tok)


# ---------------------------------------------------------------- 单位
# 单位 → (量纲, 换算到基准单位的系数). 时长基准 = 秒, 长度 = 米, 质量 = 克
_Y = 365.25 * 86400
UNITS = [
    ("光年", "light_year", 1), ("公里", "length", 1000), ("千米", "length", 1000), ("km", "length", 1000),
    ("厘米", "length", 0.01), ("cm", "length", 0.01), ("毫米", "length", 0.001), ("mm", "length", 0.001),
    ("毫秒", "time", 0.001), ("ms", "time", 0.001),
    ("米", "length", 1), ("m", "length", 1),
    ("小时", "time", 3600), ("钟头", "time", 3600), ("分钟", "time", 60), ("分", "time", 60),
    ("秒钟", "time", 1), ("秒", "time", 1), ("s", "time", 1), ("天", "time", 86400),
    ("星期", "time", 7 * 86400), ("周", "time", 7 * 86400), ("月", "time", _Y / 12), ("年", "time", _Y),
    ("美元", "money:USD", 1), ("美金", "money:USD", 1), ("欧元", "money:EUR", 1), ("日元", "money:JPY", 1),
    ("港元", "money:HKD", 1), ("港币", "money:HKD", 1), ("英镑", "money:GBP", 1),
    ("人民币", "money:CNY", 1), ("块钱", "money:CNY", 1), ("块", "money:CNY", 1), ("元", "money:CNY", 1),
    ("个百分点", "percent", 1), ("%", "percent", 1), ("％", "percent", 1),
    ("岁", "age", 1), ("倍", "times", 1), ("人", "people", 1), ("圈", "laps", 1),
    ("公斤", "mass", 1000), ("千克", "mass", 1000), ("kg", "mass", 1000), ("吨", "mass", 1e6), ("斤", "mass", 500),
    ("克", "mass", 1), ("g", "mass", 1),
    ("摄氏度", "temp", 1), ("℃", "temp", 1), ("°C", "temp", 1),
]
UNIT_MAP = {u: (d, f) for u, d, f in UNITS}
TIME_RANK = {"年": 9, "月": 8, "星期": 7, "周": 7, "天": 6, "小时": 5, "钟头": 5, "分钟": 4, "分": 4,
             "秒钟": 3, "秒": 3, "s": 3, "毫秒": 2, "ms": 2}
PER_SEC = {"秒": 1, "秒钟": 1, "s": 1, "小时": 3600, "个小时": 3600, "h": 3600, "时": 3600, "分钟": 60}


def _alt(words):
    out = []
    for w in sorted(words, key=len, reverse=True):
        e = re.escape(w)
        out.append(e + "(?![a-zA-Z])" if w[-1].isascii() and w[-1].isalpha() else e)
    return "|".join(out)


AR = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\d)|\d+(?:\.\d+)?"
CN = r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二三四五六七八九]+[十百千万亿]*)?"
TOKEN = re.compile(
    r"(?P<sign>[+\-−]\s?)?(?P<pre>US\$|\$|¥|￥|百分之)?"
    rf"(?P<num>{AR}|{CN}|半)"
    r"(?P<more>多|余)?"
    r"(?P<mag>\s?(?:[万亿千]+|[kKMB](?![a-zA-Z])))?"
    r"\s*(?P<ge>个)?(?P<h1>半)?"
    rf"(?P<unit>{_alt(UNIT_MAP)})?"
    r"(?P<per>\s*(?:/|每)\s*(?:秒钟?|小时|s(?![a-zA-Z])|h(?![a-zA-Z])))?"
    r"(?P<h2>半)?"
    r"(?P<post>左右|上下|出头|以上|以下|多)?")
DATE = re.compile(r"(?<!\d)(1[5-9]\d\d|20\d\d)[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
PRE_APPROX = ("大约", "大概", "差不多", "约莫", "约", "左右")
PRE_MORE = ("超过", "多达", "高达", "足有", "足足", "逾", "超")
PRE_LESS = ("将近", "接近", "不到", "不足", "几乎", "近")
PER_BEFORE = re.compile(r"(?:(每|一)(秒钟?|个?小时|分钟)|(时速|秒速))(?=[^，。！？；,.!?\d]{0,6}$)")
MONEY_OK_NEXT = set("钱的吧啊呀了呢就都，。！？；、,.!? \n")


@dataclass
class Q:
    dim: str                 # time / length / speed / money:CNY / percent / year / bare …
    value: float             # 基准单位下的数值
    text: str                # 原文片段
    start: int
    end: int
    spoken: float = 0.0      # 念出来的数 (含万/亿, 不含单位换算), 给"没单位的大数字"比对用
    mode: str = "exact"      # exact / approx / more / less
    step: float = 0.0        # mode=more 时的上限步长
    arabic: bool = False
    unit: str = ""
    extra: dict = field(default_factory=dict)
    alts: list = field(default_factory=list)   # 同一说法的另一种读法 (打八折 = 80% 价格 / 降 20%)

    def ranges(self):
        return [self.range(v) for v in [self.value] + self.alts]

    def range(self, v=None):
        v = self.value if v is None else v
        if "lohi" in self.extra and v == self.value:
            return self.extra["lohi"]
        if self.dim == "year":
            return v, v
        if self.mode == "approx":
            return v * .94, v * 1.06
        if self.mode == "more":
            return v, v + (self.step or v * .5)
        if self.mode == "less":
            return v * .85, v
        return v * .985, v * 1.015


def _place(n: float) -> float:
    """三十 → 10, 三百四十 → 10, 三 → 1: "三十多万" 的 "多" 指到下一个整十"""
    n = int(round(abs(n)))
    if n < 10:
        return 1
    p = 1
    while n % (p * 10) == 0:
        p *= 10
    return p


def _mode(text, start, end, post):
    before = text[max(0, start - 4):start].rstrip()
    if post in ("左右", "上下"):
        return "approx"
    if post in ("出头", "以上", "多"):
        return "more"
    if post == "以下":
        return "less"
    for w in PRE_MORE:
        if before.endswith(w):
            return "more"
    for w in PRE_LESS:
        if before.endswith(w):
            return "less"
    for w in PRE_APPROX:
        if before.endswith(w):
            return "approx"
    return "exact"


# ---------------------------------------------------------------- 相对 / 口语量词
# 三成 / 七成五 / 七八成 / 五成以上 → 百分比; 一半 / 半数 / 过半 → 50%; 打八折 → 80% (另一读法: 降 20%);
# 翻了一番 / 翻倍 → ×2, 翻两番 → ×4 (中文 "翻 N 番" = 2^N); 增长一倍 → ×2, 增长两倍 → 2~3 倍 (口语有歧义, 取区间)
_CNALL = "零〇一二两三四五六七八九十百千万亿"
_D = r"(?:[零〇一二两三四五六七八九]|\d)"
CHENG = re.compile(rf"(?<![{_CNALL}\d.第])(?P<a>十|{_D})(?P<b>{_D})?成(?P<c>{_D}(?![{_CNALL}\d成]))?")
# "八成是忘了" (=大概) / 一成不变 / 成员 成本 成功 …: 这些 "N成" 不是比例
CHENG_NOT_NEXT = tuple("是会要就又也在没不得能想已都还可怕估") + (
    "员", "本", "交", "功", "为", "长", "立", "熟", "年", "品", "果", "绩", "型", "分", "千", "百", "群", "双", "对",
    "语", "都", "形", "片", "团", "套", "批", "倍")
ZHE = re.compile(rf"(?<![{_CNALL}\d.波])(?P<da>打了?)?(?P<n>对|{_D}(?:\.\d)?{_D}?)折(?!扣|腾|磨|叠|射|服|合|算|返|线|中|纸|页|痕)")
FAN = re.compile(rf"翻了?(?P<n>{_D})番|翻了?番|翻了?一?倍")
GROW = re.compile(rf"(?:增长|增加|上涨|提高|提升|涨|增)了?(?P<n>{CN}|\d+(?:\.\d+)?)倍")
HALF = re.compile(r"(?<![另多大])(?P<w>超过一半|超过半数|超半数|一大半|过半数?|半数|一半)")
REL_POST = ("以上", "以下", "左右", "上下", "出头", "多")


def _d(c: str) -> float:
    return float(c) if c.isdigit() else float(CN_DIG[c])


def _relative(text: str) -> list:
    """口语相对量 → [Q] (dim = percent / times); 调用方把这些片段让出来, 不再当裸数字解析"""
    out = []

    def add(dim, v, m, step=0.0, mode=None, alts=None, start=None):
        s = m.start() if start is None else start
        e = m.end()
        post = next((w for w in REL_POST if text.startswith(w, e)), None)
        if post:
            e += len(post)
        md = mode or _mode(text, s, e, post)
        if md == "more" and not step:
            step = (100 - v) if dim == "percent" else v * .5     # 五成以上: 50%~100%
        q = Q(dim, v, text[s:e], s, e, spoken=v, mode=md, step=step, unit="rel")
        q.alts = list(alts or [])
        out.append(q)

    for m in CHENG.finditer(text):
        nxt = text[m.end():m.end() + 1]
        if nxt and (nxt in CHENG_NOT_NEXT or text.startswith(("不变",), m.end())):
            continue
        a, b, c = m.group("a"), m.group("b"), m.group("c")
        if a == "十":
            if b:
                continue
            add("percent", 100.0, m)
        elif b:                                                  # 七八成 → 70%~80%
            lo, hi = sorted((_d(a) * 10, _d(b) * 10))
            if hi - lo != 10:
                continue
            add("percent", lo, m, step=10.0, mode="more")
        else:
            add("percent", _d(a) * 10 + (_d(c) if c else 0), m)
    for m in ZHE.finditer(text):
        n = m.group("n")
        if n == "对":
            if not m.group("da"):
                continue                                         # "对折" 单说是折叠
            v = 50.0
        elif "." in n:
            v = float(n) * 10                                    # 8.5折
        else:
            ds = [_d(c) for c in n]
            v = ds[0] * 10 + (ds[1] if len(ds) > 1 else 0)
        if 0 < v < 100:
            add("percent", v, m, alts=[100 - v])
    for m in FAN.finditer(text):
        n = m.group("n")
        add("times", 2.0 ** _d(n) if n else 2.0, m)
    for m in GROW.finditer(text):
        try:
            n = _num(m.group("n"))
        except (ValueError, KeyError):
            continue
        if n == 1:
            add("times", 2.0, m)                                 # 增长一倍 = 原来的 2 倍
        elif n > 1:
            add("times", n, m, step=1.0, mode="more")            # 增长两倍: 严格说 3 倍, 口语常指 2 倍 → 2~3 倍都算
    for m in HALF.finditer(text):
        w = m.group("w")
        if w in ("一半", "半数"):
            add("percent", 50.0, m)
        else:
            add("percent", 50.0, m, step=50.0, mode="more")      # 过半 / 超过一半: 下限 50%
    out.sort(key=lambda q: q.start)
    keep = []
    for q in out:                                                # 重叠时留先出现的
        if keep and q.start < keep[-1].end:
            continue
        keep.append(q)
    return keep


def parse(text: str) -> list:
    """一段文字里的全部数值 → [Q] (时长组合已合并, 速度已识别, 序数/日期月日已去掉)"""
    text = str(text or "")
    out, taken = [], []
    for m in DATE.finditer(text):                                # 1929.10.24 → 年份 1929
        out.append(Q("year", float(m.group(1)), m.group(), m.start(), m.end(), spoken=float(m.group(1)), arabic=True))
        taken.append((m.start(), m.end()))
    for q in _relative(text):                                    # 三成 / 翻一番 / 一半 / 打八折 …
        if any(a <= q.start < b for a, b in taken):
            continue
        out.append(q)
        taken.append((q.start, q.end))
    raw = []
    pos = 0
    while pos < len(text):
        m = TOKEN.search(text, pos)
        if not m:
            break
        pos = max(m.end(), m.start() + 1)
        if any(a <= m.start() < b for a, b in taken):
            continue
        raw.append(m)
    toks = []
    for m in raw:
        g = m.groupdict()
        s = m.start("num") - len(g["pre"] or "") - len(g["sign"] or "")
        if s > 0 and text[s - 1] == "第":                           # 第二幕 / 第五次: 序数不是数值
            continue
        try:
            n = _num(g["num"])
        except (ValueError, KeyError):
            continue
        arabic = g["num"][0].isdigit()
        mag = 1.0
        for c in (g["mag"] or "").strip():
            mag *= MAG[c]
        unit, pre, ge = g["unit"] or "", g["pre"] or "", g["ge"] or ""
        spoken_raw = n * mag
        nxt = text[m.end("unit"):m.end("unit") + 1] if unit else ""
        if unit == "月" and not ge:                                  # 10月 是日期; 只认 "三个月"
            unit = ""
        if unit == "块" and nxt and nxt not in MONEY_OK_NEXT and spoken_raw < 10:
            unit = ""                                                # 一块泥巴 ≠ 一块钱 (十块以上照算钱: 一千三百块买手机)
        if unit == "年" and text[m.end("unit"):m.end("unit") + 1] == "代":   # 九十年代
            continue
        if unit == "人" and nxt and nxt in "们生物类民气":            # 人们 / 人生
            unit = ""
        end = m.end("unit") if unit else m.end("mag") if g["mag"] else m.end("more") if g["more"] else m.end("num")
        if unit and g["per"]:
            end = m.end("per")
        if unit and g["h2"]:
            end = m.end("h2")
        if g["post"] and (unit or pre):
            end = m.end("post")
        spoken = n * mag
        half = (.5 if g["h1"] else 0) + (.5 if (g["h2"] and unit) else 0)
        mode = _mode(text, s, end, g["post"] if (unit or pre) else None)
        step = 0.0
        if g["more"]:
            mode, step = "more", _place(n) * mag
        elif mode == "more" and g["post"] == "多" and unit:
            step = 1.0
        q = None
        if pre == "百分之" or unit in ("%", "％", "个百分点"):
            q = Q("percent", spoken, "", s, end)
        elif pre in ("$", "US$"):
            q = Q("money:USD", spoken, "", s, end)
        elif pre in ("¥", "￥"):
            q = Q("money:CNY", spoken, "", s, end)
        elif unit:
            dim, f = UNIT_MAP[unit]
            v = spoken + half
            if unit == "年" and not ge and not half and mag == 1 and 1000 <= n <= 2100 and \
                    (len(g["num"]) == 4) and text[end:end + 1] not in ("后", "前", "间"):
                dim, f = "year", 1                                   # 1929年 / 一九二九年 是年份不是时长
            if g["per"] and dim == "length":
                per = re.sub(r"[\s/每]", "", g["per"])
                dim, f = "speed", f / PER_SEC[per]
            q = Q(dim, v * f, "", s, end)
            if step:
                q.step = step * f
            elif mode == "more" and g["post"] == "多":
                q.step = f
        else:
            if arabic and not g["mag"] and re.fullmatch(r"1[5-9]\d\d|20\d\d", g["num"]):
                q = Q("year", n, "", s, end)                         # 第二幕 · 1907 / kicker 1891
            else:
                q = Q("bare", spoken, "", s, end)
        q.text, q.spoken, q.arabic, q.unit = text[s:end], spoken + half, arabic, unit
        q.mode = q.mode if q.dim == "year" else mode
        if mode == "more" and not q.step:
            q.step = q.value * .5
        toks.append(q)
    toks = _merge_durations(text, toks)
    _speed_from_context(text, toks)
    toks = [q for q in toks if not q.extra.get("ambiguous")]
    return sorted(out + toks, key=lambda q: q.start)


def _merge_durations(text, toks):
    """八分二十秒 / 1 小时 20 分 / 一天三小时 → 一个时长; 单独的 "N分" 有歧义 (十分重要 / 三分之一 / 99分) 丢掉"""
    out = []
    for q in toks:
        if q.dim == "time" and q.unit == "分" and not q.text.endswith("半"):
            q.extra["ambiguous"] = True
        prev = out[-1] if out else None
        if prev and prev.dim == "time" and q.dim == "time" and \
                re.fullmatch(r"\s*(?:零|又)?\s*", text[prev.end:q.start]) and \
                TIME_RANK.get(prev.extra.get("last_unit", prev.unit), 0) > TIME_RANK.get(q.unit, 0):
            prev.value += q.value
            prev.end = q.end
            prev.text = text[prev.start:q.end]
            prev.extra["ambiguous"] = False
            prev.extra["last_unit"] = q.unit
            if q.mode in ("more", "less", "approx") and prev.mode == "exact":
                prev.mode, prev.step = q.mode, q.step
            continue
        out.append(q)
    return out


def _speed_from_context(text, toks):
    """每秒三十万公里 / 光一秒钟能跑约三十万公里 / 时速 300 公里 → 速度"""
    for q in toks:
        if q.dim != "length":
            continue
        m = PER_BEFORE.search(text[max(0, q.start - 12):q.start])
        if not m:
            continue
        if m.group(3):
            per = 1 if m.group(3) == "秒速" else 3600
        else:
            per = PER_SEC[m.group(2).replace("个", "")] if m.group(2).replace("个", "") in PER_SEC else None
            if per is None:
                continue
        q.dim, q.value = "speed", q.value / per
        if q.step:
            q.step /= per


# ---------------------------------------------------------------- 画面字段
TEXT_KEYS = {
    "hook": ("title", "tape"),
    "headline": ("kicker", "title", "lines"),
    "stamps": ("title", "stamps"),
    "counter": ("label", "note"),
    "versus": ("left.name", "left.value", "right.name", "right.value"),
    "quote": ("quote",),          # endcard 是"关注我 · 下期讲…", 不是内容, 不查
    "image": ("caption",),
}
NUM_KEYS = {"hook": ("number",), "counter": ("from", "to")}
SKIP_KEYS = {"src", "href", "url", "img", "image", "icon", "color", "bg", "css", "class", "style", "font", "id",
             "endcard", "x", "y", "w", "h", "left_px", "top", "size", "seed"}
CUSTOM_NUM_KEYS = {"number", "value", "from", "to", "num", "count", "amount", "total", "price", "percent", "pct", "target"}


def render_num(v, p, decimals=False) -> str:
    """跟 engine.js 的 num() 一样: 符号 + prefix + 四舍五入千分位整数 + suffix。
    decimals=True (自定义版式): 参数里写了 "decimals" 就按它保留小数 —— custom.js 常用 v.toFixed(p.decimals),
    原来一律取整, 把 gauge 的 4.7% 当成 5% 去跟旁白"百分之四点七"对账, 误报矛盾。"""
    a = abs(float(v))
    d = p.get("decimals") if decimals else None
    if isinstance(d, int) and not isinstance(d, bool) and d > 0:
        body = f"{a:,.{d}f}"
    else:
        body = f"{int(math.floor(a + .5)):,}"
    return ("-" if v < 0 else "") + str(p.get("prefix") or "") + body + str(p.get("suffix") or "")


def _get(p, path):
    for k in path.split("."):
        p = p.get(k) if isinstance(p, dict) else None
    return p


def screen_fields(scene) -> list:
    """[(字段名, 画面上的文字, 是否数字参数)] —— engine.js 各版式真正画到画面上的东西"""
    p = scene.get("params") or {}
    t = scene.get("type", "")
    out = []
    if scene.get("act"):
        out.append(("act", str(scene["act"]), False))
    if t in TEXT_KEYS:
        for k in TEXT_KEYS[t]:
            v = _get(p, k)
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, (str, int, float)) and str(x).strip():
                    out.append((k, str(x), False))
        for k in NUM_KEYS.get(t, ()):
            v = p.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((k, render_num(v, p), True))
        return out

    def walk(o, path):                                            # 自定义版式 (custom.js): 扫字符串 + 常见数值键
        if isinstance(o, dict):
            for k, v in o.items():
                if k in SKIP_KEYS:
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    if k in CUSTOM_NUM_KEYS:
                        out.append((path + k, render_num(v, o, decimals=True), True))
                else:
                    walk(v, path + k + ".")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path[:-1] if path.endswith('.') else path}[{i}].")
        elif isinstance(o, str) and o.strip() and not re.match(r"^(#|rgb|https?:|\.{0,2}/|[\w-]+\.(png|jpe?g|webp|svg|gif)$)", o):
            out.append((path.rstrip("."), o, False))
    walk(p, "")
    return out


# ---------------------------------------------------------------- 对账
def _match(a: Q, b: Q) -> bool:
    for lo1, hi1 in a.ranges():
        for lo2, hi2 in b.ranges():
            eps = 1e-9 * max(abs(hi1), abs(hi2), 1)
            if lo1 <= hi2 + eps and lo2 <= hi1 + eps:
                return True
    return False


def _match_bare(x: Q, b: Q) -> bool:
    """没单位的画面数字 跟 旁白任意数值比: 比基准值, 也比念出来的数 (三十万公里 → 300000)"""
    for c in {b.value, b.spoken}:
        if c and b.value:
            k = c / b.value
            lo, hi = b.range()
            lo, hi = lo * k, hi * k
            xl, xh = x.range()
            if xl <= hi * (1 + 1e-9) and lo <= xh * (1 + 1e-9):
                return True
    return False


def _fmt(q: Q) -> str:
    v = q.value
    if q.dim == "time":
        if v >= 86400 * 2:
            return f"{v / 86400:g} 天"
        s = int(round(v))
        h, mi, se = s // 3600, s % 3600 // 60, s % 60
        parts = ([f"{h} 小时"] if h else []) + ([f"{mi} 分"] if mi else []) + ([f"{se} 秒"] if se or not (h or mi) else [])
        return " ".join(parts) + (f" (= {s} 秒)" if h or mi else "")
    unit = {"length": "米", "speed": "米/秒", "percent": "%", "year": "年", "mass": "克", "age": "岁", "times": "倍",
            "people": "人", "laps": "圈", "temp": "℃", "light_year": "光年"}.get(q.dim, "")
    if q.dim.startswith("money:"):
        unit = q.dim[6:]
    return f"{v:,.6g} {unit}".strip()


DIM_CN = {"time": "时长", "length": "长度", "speed": "速度", "percent": "百分比", "year": "年份", "mass": "重量",
          "age": "年龄", "times": "倍数", "people": "人数", "laps": "圈数", "temp": "温度", "light_year": "光年"}


def _dim_cn(d):
    return "金额(" + d[6:] + ")" if d.startswith("money:") else DIM_CN.get(d, d)


def facts_quantities(sj) -> list:
    qs = []
    for f in sj.get("facts") or []:
        if f.get("year"):
            try:
                y = float(f["year"])
                qs.append(Q("year", y, str(f["year"]), 0, 0, spoken=y))
            except (TypeError, ValueError):
                pass
        qs += parse(f"{f.get('fact', '')} ; {f.get('source', '')}")
    return qs


def _say(q: Q) -> str:
    return ("约" if q.mode == "approx" and not q.text.startswith("约") else "") + q.text


def _nearest(qs, v):
    return min(qs, key=lambda q: abs(math.log(q.value / v)) if q.value > 0 and v > 0 else 1e9)


# ---------------------------------------------------------------- 画面字段互相对账
BIG_KEYS = {"number", "to", "value", "target", "total", "count", "amount"}   # hook.number / counter.to / 自定义版式顶层大数字
DELTA_BEFORE = ("比", "多了", "少了", "快了", "慢了", "增", "减", "涨", "跌", "降", "提升", "提高", "下降", "差", "剩",
                "还有", "再", "相差", "领先", "落后")


def _is_delta(txt: str, q: Q) -> bool:
    """「比去年多 30 秒」「降 20%」这类写的是差值 / 变化量, 不是同一个数, 不拿来对账"""
    if txt[q.start:q.start + 1] in "+-−":
        return True
    before = txt[max(0, q.start - 6):q.start]
    return any(w in before for w in DELTA_BEFORE)


def _param_tolerance(sq: Q) -> Q:
    """数字参数在画面上被四舍五入成整数 (7.5 圈 → 8圈): 容差放宽到 ±0.5 个念出来的单位"""
    if sq.spoken:
        f = sq.value / sq.spoken
        lo, hi = sq.range()
        sq.extra["lohi"] = (min(lo, (sq.spoken - .5) * f), max(hi, (sq.spoken + .5) * f))
    return sq


def cross_check(s: dict, shown: list, skip_fields: set, sourced) -> list:
    """同一场里, 大数字 (hook.number / counter.to …, 带单位) ↔ 同场 note/caption/副标题等文字里写出的同量纲数字。
    只在同量纲、两边都明确解析出数值时判定; 文字里写的是差值 (比…多 / 降 …) 不算; 一个大数字只报一次。"""
    sid, typ = s.get("id", "?"), s.get("type", "")
    custom = typ not in TEXT_KEYS
    errs = []
    bigs = [(f, t, q) for f, t, is_param, q in shown if is_param and f in BIG_KEYS and q.dim not in ("bare", "year")
            and f not in skip_fields]
    texts = [(f, t, q) for f, t, is_param, q in shown if not is_param and f != "act"
             and f not in skip_fields and not (custom and ("." in f or "[" in f))
             and q.dim not in ("bare", "year") and not _is_delta(t, q)]
    for bf, bt, bq in bigs:
        bq = copy.copy(bq)
        bq.extra = dict(bq.extra)                                # 别改到原对象
        _param_tolerance(bq)
        by_field = {}
        for tf, tt, tq in texts:
            if tq.dim == bq.dim:
                by_field.setdefault((tf, tt), []).append(tq)
        for (tf, tt), qs in by_field.items():
            if any(_match(bq, q) for q in qs):
                continue
            near = _nearest(qs, bq.value)
            kind = _dim_cn(bq.dim)
            diff = abs(near.value - bq.value) / max(near.value, bq.value) * 100
            ok_b, ok_t = sourced(bq), sourced(near)
            hint = ("大数字有出处 (旁白/facts), 改文字" if ok_b and not ok_t else
                    "文字这个数有出处 (旁白/facts), 改大数字" if ok_t and not ok_b else "以 facts 为准把两处统一")
            errs.append(f"{sid}: 画面 {typ}.{bf}「{bt}」= {_fmt(bq)}, 同场画面 {typ}.{tf}「{tt}」写的是「{_say(near)}」≈ {_fmt(near)}"
                        f" —— 画面自相{kind}矛盾 (差 {diff:.0f}%); {hint}")
            break
    return errs


def check(sj: dict):
    """→ (errors, warnings). errors = 画面与旁白矛盾 / 同场画面字段互相矛盾 (硬闸); warnings = 画面数字没有出处 (提示)"""
    scenes = sj.get("scenes") or []
    narr = [parse(s.get("text", "")) for s in scenes]
    facts = facts_quantities(sj)
    errs, warns = [], []
    for i, s in enumerate(scenes):
        sid = s.get("id", f"#{i}")
        ignore = [x for x in (s.get("numcheck_ignore") or []) if x]
        mine = narr[i]
        support = [q for j, qs in enumerate(narr) if j != i for q in qs] + facts
        shown = [(fname, txt, is_param, sq) for fname, txt, is_param in screen_fields(s)
                 if not any(x in txt for x in ignore) for sq in parse(txt) if sq.value != 0]
        blamed = set()                                               # 同一句旁白只报一次
        bad = set()                                                  # 已经因旁白矛盾报过错的画面字段 (互相对账不再重复报)
        for fname, txt, is_param, sq in shown:
            where = f"{sid}: 画面 {s.get('type', '')}.{fname}「{txt}」"
            if sq.dim == "bare":
                if not (is_param or sq.arabic):
                    continue                                         # 标题里的 "活字三步" 之类不查
                if any(_match_bare(sq, q) for q in mine + support):
                    continue
                if is_param and any(q.dim != "bare" for q in mine):
                    bad.add(fname)
                    near = ", ".join(f"「{_say(q)}」" for q in mine if q.dim != "bare")
                    errs.append(f"{where} 的大数字 {sq.value:,.6g} 跟本场旁白的 {near} 都对不上, facts 里也没有 —— "
                                f"以 facts 为准改画面或旁白 (这个数有单位就写进 prefix/suffix)")
                else:
                    warns.append(f"{where} 的数字 {sq.value:,.6g} 旁白和 facts 里都没有 —— 核对出处")
                continue
            same = [q for q in mine if q.dim == sq.dim]
            if any(_match(sq, q) for q in same):
                continue
            kind = _dim_cn(sq.dim)
            if any(q.dim == sq.dim and _match(sq, q) for q in support):
                # 画面这个数有出处 (别的场讲过 / facts 有, 如 VS 卡片的另一边);
                # 但本场旁白若念了一个同类数字, 画面上没有、facts 也没有 → 是旁白念错了
                for n in same:
                    if n.start in blamed or any(x.dim == n.dim and _match(n, x) for _, _, _, x in shown) \
                            or any(f.dim == n.dim and _match(n, f) for f in facts):
                        continue
                    blamed.add(n.start)
                    errs.append(f"{sid}: 旁白「{_say(n)}」≈ {_fmt(n)}, 画面 {s.get('type', '')}.{fname}「{txt}」= {_fmt(sq)} —— "
                                f"{kind}矛盾; 画面的数有出处, 旁白这个数 facts 里没有, 改旁白")
                continue
            if same:
                bad.add(fname)
                near = _nearest(same, sq.value)
                diff = abs(near.value - sq.value) / max(near.value, sq.value) * 100
                errs.append(f"{where} = {_fmt(sq)}, 本场旁白「{_say(near)}」≈ {_fmt(near)} —— {kind}矛盾 (差 {diff:.0f}%),"
                            f" 画面这个数 facts 里也没有; 以 facts 为准改画面或旁白")
            else:
                warns.append(f"{where} = {_fmt(sq)}: 旁白没说这个{kind}, facts 里也没有 —— 补 facts (带 source) 或改画面")
        pool = mine + support
        errs += cross_check(s, shown, bad, lambda q: any(x.dim == q.dim and _match(q, x) for x in pool))
    return errs, warns


def main():
    sj = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "script.json", encoding="utf-8"))
    errs, warns = check(sj)
    for e in errs:
        print("  ✗", e)
    for w in warns:
        print("  ·", w)
    print("画面数字闸:", "通过" if not errs else f"{len(errs)} 处画面数字与旁白矛盾", f"({len(warns)} 条提示)" if warns else "")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
