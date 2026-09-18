"""handlers/skills.py — list_skills, load_skill."""
from __future__ import annotations

from typing import Any

from . import register


@register("list_skills")
async def h_list_skills(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    lines = ["## 可用技能列表\n"]
    for sname, _, _, one_liner in td._SKILL_INDEX:
        lines.append(f"- **{sname}**: {one_liner}")
    if not td._SKILL_INDEX:
        return "No skills loaded.", None
    return "\n".join(lines), None


@register("load_skill")
async def h_load_skill(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    skill_name = args.get("name", "").strip()
    matched_name = None
    matched_content = None
    for sname, _, content, _ in td._SKILL_INDEX:
        if sname.lower() == skill_name.lower():
            matched_name = sname
            matched_content = content
            break
    if not matched_content:
        for sname, _, content, _ in td._SKILL_INDEX:
            if skill_name.lower() in sname.lower() or sname.lower() in skill_name.lower():
                matched_name = sname
                matched_content = content
                break
    if matched_content:
        td._record_skill_usage(matched_name)
        return f"[Skill: {matched_name}]\n{matched_content}", None
    available = ", ".join(s[0] for s in td._SKILL_INDEX)
    return f"Skill '{skill_name}' not found. Available: {available}", None
