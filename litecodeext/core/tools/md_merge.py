"""core/tools/md_merge.py — Markdown section merge / replace helpers."""
from __future__ import annotations

import re


def _merge_md_sections(existing: str, new_content: str) -> str:
    """智能合并 Markdown：将 new_content 中的内容追加到 existing 的对应 section。
    [v1.0] 追加前去重，避免重复 merge 产生重复段落。
    """
    # 解析 new_content 中的 sections
    new_sections = {}
    current_key = None
    buffer = []
    for line in new_content.splitlines():
        m = re.match(r'^(#{1,3})\s+(.+)', line)
        if m:
            if current_key and buffer:
                new_sections[current_key] = "\n".join(buffer)
            current_key = m.group(2).strip()
            buffer = []
        elif current_key:
            buffer.append(line)
        else:
            buffer.append(line)
    if current_key and buffer:
        new_sections[current_key] = "\n".join(buffer)

    if not new_sections:
        # 没有 section header，直接追加到末尾
        return existing.rstrip() + "\n\n" + new_content.strip() + "\n"

    result = existing
    for sec_name, sec_content in new_sections.items():
        # 找到已有 section，在其末尾追加（去重）
        pattern = re.compile(
            rf'(^#{1,3}\s+{re.escape(sec_name)}\s*$)(.*?)(?=^#{1,3}\s|\Z)',
            re.MULTILINE | re.DOTALL
        )
        match = pattern.search(result)
        if match:
            old_block = match.group(0)
            old_body = match.group(2).strip()
            # [v1.0] 逐行去重: 只追加 old_body 中不存在的行
            existing_lines = set(l.strip() for l in old_body.splitlines() if l.strip())
            new_lines = []
            for line in sec_content.strip().splitlines():
                if line.strip() and line.strip() not in existing_lines:
                    new_lines.append(line)
            if new_lines:
                new_block = old_block.rstrip() + "\n" + "\n".join(new_lines) + "\n"
                result = result.replace(old_block, new_block, 1)
            # else: all lines already exist, skip
        else:
            # section 不存在，追加
            result = result.rstrip() + f"\n\n## {sec_name}\n{sec_content.strip()}\n"
    return result


def _replace_md_section(existing: str, section_name: str, new_content: str) -> str:
    """替换 Markdown 中指定 section 的内容。"""
    pattern = re.compile(
        rf'(^#{1,3}\s+{re.escape(section_name)}\s*$)(.*?)(?=^#{1,3}\s|\Z)',
        re.MULTILINE | re.DOTALL
    )
    match = pattern.search(existing)
    if match:
        heading = match.group(1)
        replacement = heading + "\n" + new_content.strip() + "\n\n"
        return pattern.sub(replacement, existing, count=1)
    # section 不存在，追加
    return existing.rstrip() + f"\n\n## {section_name}\n{new_content.strip()}\n"
