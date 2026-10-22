"""handlers/skills.py — list_skills, load_skill."""
from __future__ import annotations

from typing import Any

from . import register


@register("list_skills")
async def h_list_skills(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    from lib.skills import ensure_fresh
    ensure_fresh()   # 技能在面板里被安装/停用后, 下一次调用就能看到
    lines = ["## 可用技能列表\n"]
    for sname, _, _, one_liner in td._SKILL_INDEX:
        lines.append(f"- **{sname}**: {one_liner}")
    if not td._SKILL_INDEX:
        return "No skills loaded.", None
    return "\n".join(lines), None


@register("load_skill")
async def h_load_skill(sid: str, args: dict) -> tuple[str, Any]:
    """显式加载: 读 SKILL.md 源文件全文 (不走索引里的自动注入预览)。
    正文超过 LOAD_PAGE_CHARS 时按 `## ` 章节分段, 末尾给目录 / 剩余字数 / 续读参数。"""
    import tool_dispatch as td
    from lib.skills import _SKILL_PATHS, ensure_fresh
    ensure_fresh()
    from lib.skill_body import read_source, render
    skill_name = str(args.get("name", "") or "").strip()
    section = args.get("section")
    names = [s[0] for s in td._SKILL_INDEX]
    matched_name = next((n for n in names if n.lower() == skill_name.lower()), None)
    if matched_name is None and skill_name:
        matched_name = next((n for n in names
                             if skill_name.lower() in n.lower() or n.lower() in skill_name.lower()), None)
    if matched_name is None:
        return f"Skill '{skill_name}' not found. Available: {', '.join(names)}", None
    td._record_skill_usage(matched_name)
    from lib.usage_ledger import note_skills   # [usage-ledger] 记进本轮 turn_skills
    note_skills([matched_name])
    text = read_source(_SKILL_PATHS.get(matched_name))
    if text is None:
        preview = next(c for n, _, c, _ in td._SKILL_INDEX if n == matched_name)
        return (f"[Skill: {matched_name}] (源文件读取失败, 以下是索引里的预览, 可能不完整)\n"
                f"{preview}"), None
    out, _complete = render(matched_name, text, section=section)
    return out, None
