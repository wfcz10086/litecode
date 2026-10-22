"""lib/skill_body.py — Skill 正文读取与按章节分段。

与 lib/skills.py 的分工: skills.py 只管索引里的元信息 (名称/描述/关键词) 与自动匹配,
正文一律经这里读源文件, 不走索引缓存。

- 正文 <= 上限: 整篇返回, 标"完整内容"。
- 正文 > 上限: 按 `## ` 标题切章节 (代码块里的 `##` 不算), 从指定章节开始尽量多装,
  不超过上限; 单章本身超限再按行切成"续 k/n"。结果末尾列出目录、剩余字数和续读参数,
  绝不把截断内容标成完整。
"""
from __future__ import annotations

import re
from pathlib import Path

LOAD_PAGE_CHARS = 16000      # load_skill 一次返回的正文上限 (超过即分段)
AUTO_PREVIEW_CHARS = 10000   # 按消息自动注入时的正文上限 (超过只注入开头几章 + 续读提示)
UNIT_CHARS = 8000            # 单章超过此长度再切"续 k/n"; 与分页上限无关, 保证两处给出的章节序号一致

_FENCE = re.compile(r"^\s*(```|~~~)")
_H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$")


def split_sections(text: str) -> list[tuple[str, str]]:
    """按二级标题切分 → [(标题, 原文)]。第一个标题前的部分 (含 frontmatter) 标题记为"开头"。
    各段原文首尾相接恰好还原全文。"""
    sections: list[tuple[str, list[str]]] = [("开头", [])]
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _H2.match(line.rstrip("\r\n"))
        if m:
            sections.append((m.group(1).strip(), [line]))
        else:
            sections[-1][1].append(line)
    out = [(t, "".join(ls)) for t, ls in sections]
    if out and not out[0][1].strip() and len(out) > 1:
        # 开头为空 (文件直接以 ## 起) 时并入下一段, 保证还原全文
        out[1] = (out[1][0], out[0][1] + out[1][1])
        out = out[1:]
    return out


def _split_long(title: str, body: str, limit: int) -> list[tuple[str, str]]:
    """单章超限: 按行切成若干块, 每块 <= limit (单行超限时硬切)。"""
    chunks: list[str] = []
    cur = ""
    for line in body.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    n = len(chunks)
    return [(f"{title} (续 {i}/{n})" if n > 1 else title, c) for i, c in enumerate(chunks, 1)]


def units(text: str) -> list[tuple[str, str]]:
    """章节列表, 其中每一单元都 <= UNIT_CHARS (序号对任何分页上限都一致)。"""
    out: list[tuple[str, str]] = []
    for title, body in split_sections(text):
        out.extend(_split_long(title, body, UNIT_CHARS) if len(body) > UNIT_CHARS else [(title, body)])
    return out


def _resolve_start(us: list[tuple[str, str]], section) -> int | None:
    """section: None/"" → 0; 数字 (1 起) → 下标; 文本 → 标题完全匹配优先, 再包含匹配。"""
    if section is None or str(section).strip() == "":
        return 0
    s = str(section).strip()
    if s.isdigit():
        i = int(s) - 1
        return i if 0 <= i < len(us) else None
    low = s.lower().lstrip("#").strip()
    for i, (t, _) in enumerate(us):
        if t.lower() == low:
            return i
    for i, (t, _) in enumerate(us):
        if low in t.lower():
            return i
    return None


def render(name: str, text: str, section=None, limit: int = LOAD_PAGE_CHARS,
           tool_hint: bool = True) -> tuple[str, bool]:
    """渲染给模型看的 skill 正文 → (文本, 是否完整)。
    是否完整 = 本次返回的就是全文 (正文未超限且未指定从中间章节读起)。"""
    total = len(text)
    if total <= limit and (section is None or str(section).strip() in ("", "1")):
        return f"[Skill: {name}] (完整内容, 共 {total} 字)\n{text}", True
    us = units(text)
    start = _resolve_start(us, section)
    if start is None:
        toc = "\n".join(f"  {i}. {t} ({len(b)} 字)" for i, (t, b) in enumerate(us, 1))
        return (f"[Skill: {name}] 没有章节 '{section}'。共 {total} 字, 章节目录:\n{toc}\n"
                f"用 load_skill(name='{name}', section='<序号或标题>') 读取。"), False
    end, size = start, 0
    while end < len(us) and (end == start or size + len(us[end][1]) <= limit):
        size += len(us[end][1])
        end += 1
    body = "".join(b for _, b in us[start:end])
    if start == 0 and end == len(us):
        return f"[Skill: {name}] (完整内容, 共 {total} 字)\n{text}", True
    before = sum(len(b) for _, b in us[:start])
    remain = total - before - size
    rng = f"第 {start + 1} 节" if end - start == 1 else f"第 {start + 1}-{end} 节"
    head = (f"[Skill: {name}] (非完整: 全文 {total} 字, 共 {len(us)} 节; 本次返回{rng}, "
            f"{size} 字; 之前 {before} 字, 之后还剩 {remain} 字)")
    toc_lines = []
    for i, (t, b) in enumerate(us, 1):
        mark = "← 本次" if start < i <= end else ("已读/之前" if i <= start else "未读")
        toc_lines.append(f"  {i}. {t} ({len(b)} 字) [{mark}]")
    tail = ["", "---", f"[Skill: {name} 章节目录]", *toc_lines]
    if end < len(us):
        nxt = f"load_skill(name='{name}', section='{end + 1}')" if tool_hint else f"section={end + 1}"
        tail.append(f"续读: {nxt} (下一节: {us[end][0]}); 也可用 section='<标题>' 跳到任意章节。")
    else:
        tail.append(f"已到末尾; 前面的章节用 load_skill(name='{name}', section='<序号>') 回看。")
    return head + "\n" + body.rstrip("\n") + "\n" + "\n".join(tail), False


def read_source(path: Path | None) -> str | None:
    """读 SKILL.md 源文件全文; 失败返回 None。"""
    if path is None:
        return None
    try:
        return Path(path).read_text(errors="replace")
    except Exception:
        return None
