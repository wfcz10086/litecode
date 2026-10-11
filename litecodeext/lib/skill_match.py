"""skill_match.py — 技能关键词命中打分 (纯函数)。

旧规则"重合关键词 ≥2 个即命中"在中文上太松:
  - 一个短语会被切成多个互相包含的片段, "写一个" 同时贡献 写一/一个/写一个 三个命中;
  - "帮我/一个/一下" 这类功能词和技能描述里的套话 (触发词/必须使用本技能) 都在算分。
这里把互相包含的命中折叠成一个, 并去掉功能词与高频套话后再计数。
"""
from __future__ import annotations

import re
from collections import Counter

# 用户消息里常见、与技能主题无关的片段
GENERIC_CN = frozenset({
    "帮我", "帮忙", "给我", "我写", "写一", "一个", "一下", "一份", "一部", "一篇", "一些", "这个", "那个",
    "请你", "你帮", "我想", "我要", "需要", "可以", "怎么", "什么", "如何", "为什么", "现在", "然后",
    "以后", "之前", "之后", "所有", "全部", "我们", "你们", "他们", "问题", "东西", "看看", "看下",
    "记住", "按照", "写一个", "帮我写", "帮我做", "做一个", "给我写", "生成一", "这样", "那样", "是否", "以及", "或者", "进行", "使用", "用户",
    "一句", "确认", "输出", "回复", "个人", "内容", "信息", "结果", "时候", "开始", "完成", "直接",
    "自己", "已经", "一样", "一直", "不要", "不是", "没有", "还有", "就是", "一起", "下面", "上面",
})

# 出现在这么多技能里的词视为套话, 不参与打分
DF_CUTOFF = 8


def alias_hit(alias: str, msg_lower: str) -> bool:
    """中文别名按子串; 英文别名按整词 (否则 "emo" 会命中 memory/demo/remove)。"""
    if not alias.isascii():
        return alias in msg_lower
    if alias.startswith("."):
        return re.search(re.escape(alias) + r"(?![a-z0-9])", msg_lower) is not None
    return re.search(r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])", msg_lower) is not None


def boilerplate(keyword_sets: list[set]) -> frozenset:
    df = Counter(w for kws in keyword_sets for w in kws)
    return frozenset(w for w, n in df.items() if n >= DF_CUTOFF)


def fold_nested(hits: set) -> set:
    """互相包含的命中只留最长的那个: {写一, 一个, 写一个} → {写一个}。"""
    keep = set()
    for h in sorted(hits, key=len, reverse=True):
        if not any(h in k for k in keep):
            keep.add(h)
    return keep


def score(skill_keywords: set, msg_terms: set, drop: frozenset = frozenset()) -> tuple[int, set]:
    hits = (skill_keywords & msg_terms) - GENERIC_CN - drop
    folded = {h for h in fold_nested(hits) if h not in GENERIC_CN}
    return len(folded), folded
