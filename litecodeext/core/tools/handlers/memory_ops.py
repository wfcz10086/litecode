"""handlers/memory_ops.py — update_profile, save_memory."""
from __future__ import annotations

from typing import Any

from . import register


@register("update_profile")
async def h_update_profile(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    fname = args.get("file", "USER.md")
    action = args.get("action", "read")
    if fname not in ("USER.md", "TOOLS.md", "SOUL.md", "IDENTITY.md", "HEARTBEAT.md", "BOOTSTRAP.md"):
        return f"ERROR: only USER.md / TOOLS.md / SOUL.md / IDENTITY.md / HEARTBEAT.md / BOOTSTRAP.md can be updated, got '{fname}'", None
    sess_dir = td.SESSIONS_DISK / sid if sid else None
    target_path = None
    for d in ([sess_dir] if sess_dir else []) + [td.TEMPLATE_DIR, td.BASE / "workspace_template"]:
        if d and (d / fname).exists():
            target_path = d / fname
            break
    if action == "read":
        if target_path:
            content = target_path.read_text(errors="replace")
            return f"[{fname} @ {target_path}]\n{content}", None
        return f"[{fname}] 文件不存在，可以用 action='merge' 创建", None
    if sess_dir:
        sess_dir.mkdir(parents=True, exist_ok=True)
        write_path = sess_dir / fname
    else:
        write_path = td.TEMPLATE_DIR / fname
    existing = write_path.read_text(errors="replace") if write_path.exists() else (
        target_path.read_text(errors="replace") if target_path else ""
    )
    new_content = args.get("new_content", "")
    if not new_content:
        return "ERROR: new_content is required for merge/replace_section", None
    if action == "merge":
        merged = td._merge_md_sections(existing, new_content)
        write_path.write_text(merged)
        return f"已更新 {fname} ({write_path})，当前内容:\n{merged}", None
    elif action == "replace_section":
        section = args.get("section", "")
        if not section:
            return "ERROR: section is required for replace_section", None
        replaced = td._replace_md_section(existing, section, new_content)
        write_path.write_text(replaced)
        return f"已替换 {fname} 中的 [{section}] 段落 ({write_path})", None
    return f"ERROR: unknown action '{action}'", None


@register("save_memory")
async def h_save_memory(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    if not td.HAS_MEMORY:
        return "记忆系统未启用", None
    if not sid:
        return "ERROR: save_memory requires a session", None
    section = args.get("section", "Key References")
    content = args.get("content") or ""
    topic = args.get("topic")
    if not content:
        return "ERROR: content is required", None
    try:
        mgr = td._mem_get(
            workspace=td.WORKSPACE, session_id=sid,
            vllm_url=td.BACKEND_URL, model_id=td.MODEL_ID, api_key=td.API_KEY,
            context_window=td.CONTEXT_WINDOW,
        )
        mgr.save(section, content, topic=topic)
        if td.HAS_MEMORY_INDEX:
            try:
                _midx = td.memory_index_get(td.WORKSPACE)
                _cat = "error" if section.lower() in ("errors & corrections", "errors") else \
                       "decision" if "decision" in section.lower() else "milestone"
                _midx.index_memory(sid, _cat, content[:500],
                                   solution=topic or "", tags=section.lower().replace(" ", "-"))
            except Exception:
                pass
        return f"已保存到 MEMORY.md [section={section}]" + (f" [topic={topic}]" if topic else ""), None
    except Exception as e:
        return f"ERROR saving memory: {e}", None
