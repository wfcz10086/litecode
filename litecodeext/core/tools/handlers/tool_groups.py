"""handlers/tool_groups.py — enable_tools: 为本会话加入低频工具组 (lib/tool_groups.py)。"""
from __future__ import annotations

from typing import Any

from lib import tool_groups as tg

from . import register


@register("enable_tools")
async def h_enable_tools(sid: str, args: dict) -> tuple[str, Any]:
    groups = tg.parse_groups((args or {}).get("groups"))
    if not groups:
        return f"ERROR: 未识别的组。可用组: {', '.join(tg.GROUPS)}", None
    new = tg.activate(sid, groups)
    names = [t for g in tg.GROUPS if g in groups for t in tg.GROUPS[g]["tools"]]
    head = "已加入" if new else "这些组本会话已启用"
    return f"{head}: {', '.join(g for g in tg.GROUPS if g in groups)}。下一步起可调用: {', '.join(names)}", None
