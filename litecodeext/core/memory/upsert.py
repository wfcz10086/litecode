"""memory/upsert.py — MEMORY.md 段落内的条目写入: 同 key 覆盖, 不再无限追加。

条目是段落里以 "- " 开头的行。key 的来源:
  - 调用方显式给 key (save_memory 工具的 key 参数);
  - 条目本身是 "左边 = 右边" 形式且左边不长 (≤ 40 字), 左边就是 key,
    例如规则提取的 "用户事实: 我的 项目 = PELICAN-7"。
同 key 新值不同 → 替换旧行, 并在行尾保留一处 "（曾为: 旧值）" 便于追溯;
没有 key 的条目按整行去重。SINGLE_SLOT 里的段落只保留最新一条。
"""
from __future__ import annotations

import re

SINGLE_SLOT = {"User Intent"}
_KEY_MAX = 40
_WAS_RE = re.compile(r"\s*（曾为: .*?）\s*$")
_NOT_KEY = ("`", "://", "?", "&", "/")   # URL 查询串 / 命令里的 = 不算 key
_ASSIGN_RE = re.compile(r"^(?P<k>[^=\n]{1,%d}?)\s*=\s*(?P<v>.+)$" % _KEY_MAX)


def split_kv(entry: str) -> tuple[str | None, str]:
    """'- k = v（曾为: x）' → ('k', 'v'); 无 key → (None, 原文)。"""
    body = entry.strip()
    if body.startswith("- "):
        body = body[2:]
    body = _WAS_RE.sub("", body).strip()
    m = _ASSIGN_RE.match(body)
    if not m or any(c in m.group("k") for c in _NOT_KEY):
        return None, body
    return m.group("k").strip(), m.group("v").strip()


def _key_of(line: str, key: str | None) -> tuple[str | None, str]:
    """显式 key 可能含 / 等字符, 按 '- key = ' 前缀认; 其余走 split_kv。"""
    body = line.strip()
    if key and body.startswith(f"- {key} = "):
        return key, _WAS_RE.sub("", body[len(key) + 5:]).strip()
    return split_kv(line)


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """heading 段正文的 [start, end) (到下一个 '## ' 为止); 段不存在返回 None。"""
    start = text.find(heading + "\n")
    if start < 0:
        if text.endswith(heading):
            return len(text), len(text)
        return None
    body_start = start + len(heading) + 1
    end = text.find("\n## ", body_start)
    return body_start, (len(text) if end < 0 else end + 1)


def upsert_entry(text: str, heading: str, content: str, key: str | None = None) -> tuple[str, str]:
    """返回 (新全文, 动作), 动作 ∈ added / updated / unchanged。"""
    content = " ".join(content.strip().splitlines())
    if key:
        key, value = key.strip(), _WAS_RE.sub("", content).strip()
        entry = f"- {key} = {value}"
    else:
        entry = f"- {content}"
        key, value = split_kv(entry)

    span = _section_span(text, heading)
    if span is None:
        sep = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{sep}\n{heading}\n{entry}\n", "added"
    s, e = span
    lines = text[s:e].splitlines()
    section = heading[3:] if heading.startswith("## ") else heading

    if section in SINGLE_SLOT:
        if any(ln.strip() == entry for ln in lines):
            return text, "unchanged"
        kept = [ln for ln in lines if not ln.lstrip().startswith("- ")]
        return text[:s] + "\n".join([entry] + kept) + "\n" + text[e:], "updated"

    for i, ln in enumerate(lines):
        if not ln.lstrip().startswith("- "):
            continue
        k, v = _key_of(ln, key)
        if key is not None and k == key:
            if v == value:
                return text, "unchanged"
            lines[i] = f"{entry}（曾为: {v[:80]}）"
            return text[:s] + "\n".join(lines) + "\n" + text[e:], "updated"
        if key is None and ln.strip() == entry.strip():
            return text, "unchanged"
    return text[:s] + "\n".join([entry] + lines) + ("\n" if lines else "") + text[e:], "added"


def upsert_topic_section(text: str, section: str, content: str) -> tuple[str, str]:
    """L2 主题文件: 同名 '## section' 段整体替换 (旧版首行留作曾为), 不再重复追加同名段。"""
    heading = f"## {section}"
    content = content.strip()
    span = _section_span(text, heading)
    if span is None:
        sep = "" if text.endswith("\n") else "\n"
        return f"{text}{sep}\n{heading}\n{content}\n", "added"
    s, e = span
    old = "\n".join(ln for ln in text[s:e].splitlines() if not ln.startswith("_曾为_:")).strip()
    if old == content:
        return text, "unchanged"
    was = old.splitlines()[0][:80] if old else ""
    body = content + (f"\n_曾为_: {was}" if was else "")
    return text[:s] + body + "\n" + text[e:], "updated"
