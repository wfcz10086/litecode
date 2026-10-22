"""handlers/memory_ops.py — update_profile, save_memory.

写入范围 (返回文案必须如实说明范围, 不能把只写本会话的说成"已写入长期"):
  - 全局用户档案 (core/memory/profile.py, 所有会话共享): 用户偏好/身份类事实、USER.md;
  - 本会话记忆 (sessions/<sid>/MEMORY.md 与 memory/*.md): 项目进度、决策、路径、错误经验;
  - TOOLS.md / SOUL.md 等其余上下文文件: 仍是本会话副本。
来源是外部内容 (lib/agent/untrusted.py 打了 MEMORY_TAG) 的写入一律不进全局。
"""
from __future__ import annotations

from typing import Any

from . import register

try:
    from memory import facts as _facts, profile as _profile      # 容器 / 服务进程: core 在 sys.path
except ImportError:                                              # 宿主机测试
    from core.memory import facts as _facts, profile as _profile

UNTRUSTED_TAG = "[来源=外部内容"
USER_SECTIONS = {"user preferences", "preferences", "user profile", "profile", "identity", "user",
                 "用户偏好", "偏好", "用户档案", "档案", "身份", "个人信息", "用户信息", "长期偏好"}
SESSION_SECTIONS = {"vault", "errors & corrections", "errors", "completed work", "in-progress", "next steps",
                    "question log", "code architecture", "user intent"}


def _route(section: str, content: str, key: str | None, scope: str, topic: str | None) -> str:
    """'user' = 全局档案, 'session' = 本会话 MEMORY.md。"""
    if UNTRUSTED_TAG in content:
        return "session"
    scope = (scope or "auto").lower()
    if scope in ("user", "global", "profile"):
        return "user"
    if scope == "session" or topic:
        return "session"
    sec = (section or "").strip().lower()
    if sec in USER_SECTIONS:
        return "user"
    if sec in SESSION_SECTIONS:
        return "session"
    if _facts.extract(content).key:
        return "user"
    if not key and _facts.is_durable_preference(content):
        return "user"
    return "session"


def _ws():
    import tool_dispatch as td
    return td.WORKSPACE


def _store(sid: str):
    return _profile.ProfileStore.for_session(_ws(), sid)


def _save_global(sid: str, content: str, key: str | None) -> str:
    store = _store(sid)
    action, fact, old = store.upsert(content, key=key, source="tool", session_id=sid or "")
    if action == "rejected":
        return "未写入: 内容为空或来自外部内容 (外部内容不能写进用户档案)"
    _profile.sync_index(_ws(), action, fact, old, session_id=sid or "")
    where = f"全局用户档案 (所有会话共享, profile={store.profile_id}, id={fact['id']})"
    if action == "unchanged":
        return f"{where}里已有「{fact['text']}」, 未重复写入"
    if action == "updated":
        return f"已更新{where}: 「{fact['text']}」, 替换了旧值「{old}」(旧值不再生效)"
    return f"已写入{where}: 「{fact['text']}」"


def _list(sid: str) -> str:
    store = _store(sid)
    items = store.list()
    lines = [f"## 全局用户档案 (profile={store.profile_id}, {len(items)} 条, 所有会话共享)"]
    lines += [f"- [{f['id']}] {f['text']}" for f in items] or ["(空)"]
    try:
        import tool_dispatch as td
        l1 = td.SESSIONS_DISK / sid / "MEMORY.md" if sid else None
        if l1 and l1.exists():
            from_l1 = [ln for ln in _section_lines(l1.read_text(errors="replace"), "User Preferences")]
            if from_l1:
                lines += ["", "## 本会话记下的偏好 (仅本会话)"] + from_l1
    except Exception:
        pass
    lines.append("\n要删除某条: save_memory(action='forget', key=… 或 content=…)。")
    return "\n".join(lines)


def _section_lines(text: str, section: str) -> list[str]:
    out, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("## "):
            cur = ln[3:].strip()
        elif cur == section and ln.strip().startswith("- "):
            out.append(ln.strip())
    return out


def _forget(sid: str, query: str, key: str | None) -> str:
    query = (query or "").strip()
    if not query and not key:
        return "ERROR: forget 需要 key 或 content (要忘掉的内容)"
    store = _store(sid)
    gone = store.forget(query=query, key=key or "")
    for f in gone:
        _profile.sync_index(_ws(), "forgotten", f, session_id=sid or "")
    n_l1 = 0
    if sid and query:
        try:
            import tool_dispatch as td
            if td.HAS_MEMORY:
                mgr = td._mem_get(workspace=td.WORKSPACE, session_id=sid, vllm_url=td.BACKEND_URL,
                                  model_id=td.MODEL_ID, api_key=td.API_KEY, context_window=td.CONTEXT_WINDOW)
                n_l1 = mgr.forget(query)
        except Exception:
            pass
    if not gone and not n_l1:
        return f"没找到与「{key or query}」匹配的记忆, 什么都没删。可先用 action='list' 查看。"
    parts = []
    if gone:
        parts.append("已从全局用户档案删除: " + "; ".join(f"「{f['text']}」" for f in gone))
    if n_l1:
        parts.append(f"本会话记忆里删掉 {n_l1} 行")
    return "。".join(parts)


@register("update_profile")
async def h_update_profile(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    fname = args.get("file", "USER.md")
    action = args.get("action", "read")
    if fname == "MEMORY.md":
        # 模型常拿这个工具读会话记忆: 读放行, 写引导去 save_memory (MEMORY.md 由记忆系统维护)
        if action != "read":
            return "ERROR: MEMORY.md 由记忆系统维护, 不能直接改; 要记新东西用 save_memory", None
        p = td.SESSIONS_DISK / sid / "MEMORY.md" if sid else None
        if p and p.exists():
            return f"[MEMORY.md @ {p}]\n{p.read_text(errors='replace')}", None
        return "[MEMORY.md] 本会话还没有记忆 (相关记忆已自动放在上下文里)", None
    if fname not in ("USER.md", "TOOLS.md", "SOUL.md", "IDENTITY.md", "HEARTBEAT.md", "BOOTSTRAP.md"):
        return f"ERROR: only USER.md / TOOLS.md / SOUL.md / IDENTITY.md / HEARTBEAT.md / BOOTSTRAP.md can be updated, got '{fname}'", None
    sess_dir = td.SESSIONS_DISK / sid if sid else None
    fallbacks = ([sess_dir] if sess_dir else []) + [td.TEMPLATE_DIR, td.BASE / "workspace_template"]
    if fname == "USER.md":
        target_path = _profile.resolve_user_md(td.WORKSPACE, sid, fallbacks)
    else:
        target_path = next((d / fname for d in fallbacks if d and (d / fname).exists()), None)
    if action == "read":
        if target_path:
            content = target_path.read_text(errors="replace")
            scope = "全局, 所有会话共享" if fname == "USER.md" and target_path.parent.parent.name == "profile" \
                else "模板/本会话副本"
            extra = ""
            if fname == "USER.md":
                facts = _store(sid).list()
                if facts:
                    extra = "\n\n## 已保存的用户档案事实 (save_memory 维护)\n" + "\n".join(f"- {f['text']}" for f in facts)
            return f"[{fname} @ {target_path} ({scope})]\n{content}{extra}", None
        return f"[{fname}] 文件不存在，可以用 action='merge' 创建", None
    new_content = args.get("new_content", "")
    if not new_content:
        return "ERROR: new_content is required for merge/replace_section", None
    untrusted = UNTRUSTED_TAG in new_content
    if fname == "USER.md" and not untrusted:
        write_path = _profile.profile_dir(td.WORKSPACE, sid) / "USER.md"
        scope_msg = "全局 USER.md (所有会话共享, 新会话自动带上)"
    elif sess_dir:
        write_path = sess_dir / fname
        scope_msg = f"本会话的 {fname} (仅本会话)" + (
            "; 内容来自外部内容, 未写入全局档案" if fname == "USER.md" else "")
    else:
        return f"ERROR: {fname} 只能在会话里更新", None
    write_path.parent.mkdir(parents=True, exist_ok=True)
    existing = write_path.read_text(errors="replace") if write_path.exists() else (
        target_path.read_text(errors="replace") if target_path else ""
    )
    if action == "merge":
        if fname == "USER.md":
            merged = _profile.merge_user_md(existing, new_content, td._merge_md_sections)
        else:
            merged = td._merge_md_sections(existing, new_content)
        write_path.write_text(merged)
        return f"已更新{scope_msg} ({write_path})，当前内容:\n{merged}", None
    elif action == "replace_section":
        section = args.get("section", "")
        if not section:
            return "ERROR: section is required for replace_section", None
        replaced = td._replace_md_section(existing, section, new_content)
        write_path.write_text(replaced)
        return f"已替换{scope_msg}中的 [{section}] 段落 ({write_path})", None
    return f"ERROR: unknown action '{action}'", None


@register("save_memory")
async def h_save_memory(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    action = (args.get("action") or "save").lower()
    key = args.get("key") or None
    content = args.get("content") or ""
    if action == "list":
        return _list(sid), None
    if action in ("forget", "delete"):
        return _forget(sid, content, key), None
    section = args.get("section", "Key References")
    topic = args.get("topic")
    if not content:
        return "ERROR: content is required", None
    if _route(section, content, key, args.get("scope") or "auto", topic) == "user":
        try:
            return _save_global(sid, content, key), None
        except Exception as e:
            return f"ERROR saving to user profile: {e}", None
    if not td.HAS_MEMORY:
        return "记忆系统未启用", None
    if not sid:
        return "ERROR: save_memory requires a session", None
    try:
        mgr = td._mem_get(
            workspace=td.WORKSPACE, session_id=sid,
            vllm_url=td.BACKEND_URL, model_id=td.MODEL_ID, api_key=td.API_KEY,
            context_window=td.CONTEXT_WINDOW,
        )
        saved = mgr.save(section, content, topic=topic, key=key)
        if td.HAS_MEMORY_INDEX:
            try:
                _midx = td.memory_index_get(td.WORKSPACE)
                _cat = "error" if section.lower() in ("errors & corrections", "errors") else \
                       "decision" if "decision" in section.lower() else "milestone"
                _midx.index_memory(sid, _cat, content[:500],
                                   solution=topic or "", tags=section.lower().replace(" ", "-"))
            except Exception:
                pass
        verb = {"updated": "已更新", "unchanged": "已存在, 未重复写入"}.get(saved, "已保存")
        note = ("; 内容来自外部内容, 已标记来源, 不进全局档案" if UNTRUSTED_TAG in content else
                "; 仅本会话 —— 新会话只能经关键词检索间接召回。若这是用户的长期偏好/身份信息, "
                "请用 scope='user' 写进全局档案, 回复用户时不要说成已长期保存")
        return (f"{verb}本会话记忆 MEMORY.md [section={section}]" + (f" [topic={topic}]" if topic else "")
                + note), None
    except Exception as e:
        return f"ERROR saving memory: {e}", None
