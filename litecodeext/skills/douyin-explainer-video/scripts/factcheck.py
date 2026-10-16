#!/usr/bin/env python3
"""旁白事实闸: 把旁白里的年份 / "N年后" / "第N次" / 金额 跟 script.json 的 facts 对账。
用法 (项目目录): python3 factcheck.py [script.json]      有问题 → 非 0 退出

script.json 需要:
  "facts": [{"year": 1929, "fact": "黑色星期四做空净赚约一亿美元", "source": "..."}, ...]
规则:
  1. 旁白出现的每个年份 (1929年 / 一九二九年) 都要在 facts 里有一条
  2. "N年后 / N年前" 按上一个出现的年份推算, 结果年份也必须在 facts 里 (2026-10 原片把 1929→1940 写成"十四年后")
  3. "第N次…" 必须能在某条 fact 里找到同样的说法 (次数类说法各处资料常打架, 逼着先核对)
  4. 金额 (X亿/X万/X百万 美元|元) 必须在 facts 里出现同一个数 —— 只核数字, 不管写法 (一亿 = 1亿)
"""
import json
import re
import sys

CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10 ** 4, "亿": 10 ** 8}
NUM = "零〇一二两三四五六七八九十百千万亿0-9."


def cn_int(s: str) -> float:
    """一九二九 → 1929 (逐位); 十四 / 五百 / 一亿 / 3.5亿 → 数值"""
    s = s.strip()
    if re.fullmatch(r"[\d.]+", s):
        return float(s)
    if len(s) == 4 and all(c in CN for c in s):          # 年份逐位读
        return float("".join(str(CN[c]) for c in s))
    m = re.fullmatch(r"([\d.]+)([十百千万亿]+)", s)       # 3.5亿 / 300万
    if m:
        v = float(m.group(1))
        for u in m.group(2):
            v *= UNIT[u]
        return v
    total, sec, cur = 0, 0, 0
    for c in s:
        if c in CN:
            cur = CN[c]
        elif c in ("万", "亿"):
            total += (sec + cur) * UNIT[c]; sec = cur = 0
        elif c in UNIT:
            sec += (cur or 1) * UNIT[c]; cur = 0
        else:
            raise ValueError(s)
    return float(total + sec + cur)


YEAR_RE = re.compile(r"(?<![\d一二三四五六七八九零〇])(1[5-9]\d\d|20\d\d|[一二][零〇一二三四五六七八九]{3})年(?![代后前])")
REL_RE = re.compile(rf"([{NUM}]+)年(?:后|之后|以后|过后|前|之前)")
ORD_RE = re.compile(r"第([一二三四五六七八九十\d]+)次([一-龥]{1,4})")
MONEY_RE = re.compile(rf"([{NUM}]+)(美元|美金|元|块)")


def nums_in(text: str) -> set:
    out = set()
    for m in re.finditer(rf"[{NUM}]+", text):
        try:
            out.add(cn_int(m.group()))
        except ValueError:
            pass
    return out


def check(sj: dict) -> list:
    facts = sj.get("facts") or []
    if not facts:
        return ["script.json 没有 facts —— 先把旁白里的年份/数字/次数核对后写进 facts (见 SKILL.md)"]
    years = {int(f["year"]) for f in facts if f.get("year")}
    fact_text = " ".join(f"{f.get('year', '')} {f.get('fact', '')}" for f in facts)
    fact_nums = nums_in(fact_text)
    errs, last_year = [], None
    for s in sj["scenes"]:
        text, sid = s["text"], s["id"]
        events = sorted([(m.start(), "Y", m) for m in YEAR_RE.finditer(text)] +
                        [(m.start(), "R", m) for m in REL_RE.finditer(text)], key=lambda e: e[0])
        for _, kind, m in events:
            if kind == "Y":
                y = int(cn_int(m.group(1)))
                last_year = y
                if y not in years:
                    errs.append(f"{sid}: 旁白提到 {y} 年, facts 里没有这一年")
            else:
                n = int(cn_int(m.group(1)))
                if last_year is None:
                    errs.append(f"{sid}: 「{m.group()}」前面没有出现过年份, 没法核对")
                    continue
                target = last_year - n if "前" in m.group() else last_year + n
                if target not in years:
                    near = sorted(years, key=lambda y: abs(y - target))[:1]
                    hint = f" (facts 里最近的是 {near[0]} 年 = {abs(near[0] - last_year)} 年{'前' if near[0] < last_year else '后'})" if near else ""
                    errs.append(f"{sid}: 「{m.group()}」= {last_year}{'-' if '前' in m.group() else '+'}{n} = {target} 年, facts 里没有{hint}")
                last_year = target
        for m in ORD_RE.finditer(text):
            if m.group() not in fact_text:
                errs.append(f"{sid}: 「{m.group()}」在 facts 里找不到同样说法 —— 次数先核对")
        for m in MONEY_RE.finditer(text):
            try:
                v = cn_int(m.group(1))
            except ValueError:
                continue
            if v not in fact_nums:
                errs.append(f"{sid}: 金额「{m.group()}」在 facts 里没有出现")
    return errs


def main():
    sj = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "script.json", encoding="utf-8"))
    errs = check(sj)
    for e in errs:
        print("  ✗", e)
    print("事实闸:", "通过" if not errs else f"{len(errs)} 处要核对 (改旁白, 或补 facts 并写清来源)")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
