"""handlers/kb.py — kb_search: 检索内置知识库 (实现在 plugins/kb/tool.py)。"""
from __future__ import annotations

from typing import Any

from . import register


@register("kb_search")
async def h_kb_search(sid: str, args: dict) -> tuple[str, Any]:
    from plugins.kb import tool as _kb_tool
    return await _kb_tool.run(args or {}), None
