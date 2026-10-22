"""lib/skill_search.py — 技能本地检索打分 (技能面板 / `/` 选择器 / 市场索引的本地过滤共用)。

字段与权重: 名称 > 中文名 > 别名 > 描述 / 关键词章节。整串命中加大分, 再按查询词逐个计分:
英文按单词 (>=2 字母), 中文按 2-gram (单字查询按单字)。查询词命中率不足一半的不返回,
避免 "小说" 这种查询把只沾一个字的技能也排进来。
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9][a-z0-9.+#_-]*")
_CJK = re.compile(r"[一-鿿]+")


def terms(q: str) -> list[str]:
    q = (q or "").lower()
    out = [w for w in _WORD.findall(q) if len(w) >= 2]
    for run in _CJK.findall(q):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _has(t: str, text: str) -> bool:
    """英文/数字按整词 (word 不该命中 keyword/password), 中文按子串。"""
    if t.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text) is not None
    return t in text


def score(item: dict, q: str) -> float:
    q = (q or "").strip().lower()
    if not q:
        return 1.0
    name = str(item.get("name") or "").lower()
    title = str(item.get("title") or "").lower()
    aliases = [str(a).lower() for a in item.get("aliases") or []]
    desc = str(item.get("description") or "").lower()
    kw = str(item.get("keywords") or "").lower()
    group = str(item.get("group") or "").lower()
    s = 0.0
    if q == name or q == title:
        s += 100
    elif name.startswith(q) or title.startswith(q):
        s += 50
    elif q in name or q in title:
        s += 30
    if q in aliases:
        s += 60
    elif any(q in a or (len(a) >= 2 and a in q) for a in aliases):
        s += 25
    if _has(q, desc):
        s += 15
    if q == group:
        s += 20
    elif q in group:
        s += 12
    ts = terms(q)
    if not ts:
        return s
    hit = 0
    for t in ts:
        w = 0.0
        if t in name or t in title:
            w = 6
        elif any(t in a for a in aliases):
            w = 4
        elif _has(t, desc) or _has(t, kw) or t in group:
            w = 2
        if w:
            hit += 1
            s += w
    if s < 25 and hit * 2 < len(ts):      # 整串没怎么命中, 词也命中不到一半 → 不算
        return 0.0
    return s


def search(items: list[dict], q: str, limit: int = 0) -> list[dict]:
    """按分数降序 (同分按使用次数), 分数写进 item["score"]。q 为空时原序返回。"""
    if not (q or "").strip():
        return items[:limit] if limit else list(items)
    scored = []
    for it in items:
        sc = score(it, q)
        if sc > 0:
            scored.append({**it, "score": round(sc, 1)})
    scored.sort(key=lambda x: (-x["score"], -int(x.get("use_count") or 0), x.get("name", "")))
    return scored[:limit] if limit else scored
