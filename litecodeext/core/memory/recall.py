"""memory/recall.py — 按当前用户消息挑记忆条目, 不再整份塞进上下文。

条目来源: 会话 MEMORY.md (L1) 各段的 "- " 行 + memory/*.md (L2) 各段。
打分: BM25 (纯 Python), 中文按相邻两字切词, 英文/数字按词。
选取: 常驻段 (默认 Vault / User Preferences) 全取, 其余按分数取前 k 条, 总字数封顶;
已在历史里出现过的内容 (模型本来就看得见) 跳过。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

SKIP_SECTIONS = {"User Intent", "Question Log"}   # 流水账, 不是可复用的事实
_WORD = re.compile(r"[a-z0-9_.\-]{2,}")
_CJK = re.compile(r"[一-鿿]+")
_LITERAL = re.compile(r"[A-Za-z0-9_\-]*[/.:][A-Za-z0-9_./:\-]{6,}")   # 路径 / URL / host:port


@dataclass(frozen=True)
class Entry:
    section: str
    text: str
    source: str = "L1"


# ── 中文近义归一 (没有分词/向量依赖时的折中) ─────────────────────────
# 用户问法和记下的说法常不同字: "我爱吃啥水果" vs "最喜欢的水果是山竹"。两字切词只剩"水果"一个
# 重合词, 过不了召回门槛。先把常见同义说法归到同一写法, 再去掉虚词, 两字切词的重合率就上来了。
_SYNONYMS = [(re.compile(p), r) for p, r in (
    (r"不爱|讨厌|厌恶|反感", "不喜欢"),
    (r"(?<![可恋热亲])爱(?=吃|喝|看|听|玩|用|穿|去)", "喜欢"),
    (r"最爱", "最喜欢"),
    (r"喜爱|偏爱|钟爱|钟意|中意|热爱|偏好|爱好", "喜欢"),
    (r"啥|哪种|哪样|哪个|哪些|什么样", "什么"),
    (r"咋", "怎么"),
    (r"姓名|称呼|昵称|叫什么", "名字"),
    (r"住哪儿?|住在哪里?", "住在"),
    (r"干什么的|做什么工作|从事什么|职业是|工作是", "职业"),
    (r"出生日期|哪天生|几号生", "生日"),
    (r"答复|回答", "回复"),
    (r"英语", "英文"), (r"汉语|普通话", "中文"),
)]
# 虚词/代词/疑问词: 去掉后两字切词不再产出 "我爱"/"吃啥" 这类无信息的词
_STOP_WORDS = re.compile(r"什么|怎么|请问|告诉|记得|知道|一下|一个|是不是|还记|用户|主人")
_STOP_CHARS = re.compile(r"[的了是吗呢吧啊呀么哦嘛我你他她它们]")


def normalize(text: str) -> str:
    t = text.lower()
    for pat, rep in _SYNONYMS:
        t = pat.sub(rep, t)
    return t


def tokens(text: str) -> list[str]:
    t = normalize(text)
    out = _WORD.findall(t)
    for run in _CJK.findall(t):
        run = _STOP_CHARS.sub("", _STOP_WORDS.sub(" ", run))
        for part in run.split():
            out.extend(part if len(part) == 1 else (part[i:i + 2] for i in range(len(part) - 1)))
    return out


def parse_entries(md: str, source: str = "L1") -> list[Entry]:
    out, section = [], ""
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            continue
        if not section or section in SKIP_SECTIONS:
            continue
        if source == "L1" and not s.startswith("- "):
            continue
        body = s[2:].strip() if s.startswith("- ") else s
        if body and not body.startswith(("<!--", "_曾为_:")):
            out.append(Entry(section, body, source))
    return out


def load_entries(session_dir: Path) -> list[Entry]:
    entries: list[Entry] = []
    l1 = session_dir / "MEMORY.md"
    if l1.exists():
        entries += parse_entries(l1.read_text(errors="replace"), "L1")
    for f in sorted((session_dir / "memory").glob("*.md")):
        entries += parse_entries(f.read_text(errors="replace"), f"L2:{f.stem}")
    return list(dict.fromkeys(entries))


def bm25(query: str, entries: list[Entry], k1: float = 1.5, b: float = 0.75) -> list[float]:
    docs = [tokens(f"{e.section} {e.text}") for e in entries]
    if not docs:
        return []
    avg = sum(map(len, docs)) / len(docs) or 1.0
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    q = set(tokens(query))
    n = len(docs)
    scores = []
    for d in docs:
        s = 0.0
        for t in q:
            tf = d.count(t)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avg))
        scores.append(s)
    return scores


def _visible(entry: Entry, seen: str) -> bool:
    """条目的值已出现在历史/已注入上下文里 → 模型看得见, 不必再给。"""
    if entry.text in seen:
        return True
    core = entry.text.split(": ", 1)[-1].split(" = ", 1)[-1].strip("` ")
    if len(core) >= 6 and core in seen:
        return True
    return any(lit in seen for lit in _LITERAL.findall(entry.text))


def select(query: str, entries: list[Entry], k: int = 5, pinned=("Vault", "User Preferences"),
           seen: str = "", max_chars: int = 1200) -> list[Entry]:
    fresh = [e for e in entries if not _visible(e, seen)]
    pin = [e for e in fresh if e.section in pinned]
    rest = [e for e in fresh if e.section not in pinned]
    ranked = sorted(zip(bm25(query, rest), range(len(rest))), key=lambda x: (-x[0], x[1]))
    picked = pin + [rest[i] for s, i in ranked[:k] if s > 0]
    out, used = [], 0
    for e in picked:
        if used + len(e.text) > max_chars:
            break
        out.append(e)
        used += len(e.text)
    return out


def render(entries: list[Entry]) -> str:
    if not entries:
        return ""
    lines = [f"- [{e.section}] {e.text}" for e in entries]
    return "## 相关记忆 (本会话 MEMORY.md, 按与本条消息的相关度挑选)\n" + "\n".join(lines)
