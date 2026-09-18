"""handlers/meta.py — self_reflect + create_skill (自省 / skill 草稿)."""
from __future__ import annotations

import re
import time
from typing import Any

from lib.config import HAS_MEMORY_INDEX, SKILLS_DIR, WORKSPACE, memory_index_get
from lib.skills import _SKILL_INDEX, _build_skill_index

from . import register


def _skill_usage_summary() -> str:
    """See tool_dispatch.get_skill_usage_summary — kept a thin duplicate for
    handler locality; original still lives in tool_dispatch until fully migrated."""
    try:
        from tool_dispatch import get_skill_usage_summary as _s
        return _s()
    except Exception as e:
        return f"(usage-summary import failed: {e})"


@register("self_reflect")
async def h_self_reflect(sid: str, args: dict) -> tuple[str, Any]:
    scope = args.get("scope", "errors")
    query = args.get("query", "")
    limit = int(args.get("limit", 10))

    if scope == "skill_usage":
        return _skill_usage_summary(), None

    if scope == "errors":
        if not HAS_MEMORY_INDEX:
            return "memory_index 未启用, 无法查询历史错误", None
        try:
            _midx = memory_index_get(WORKSPACE)
            if query:
                results = _midx.search(query, limit=limit, category="error")
            else:
                conn = _midx._get_conn()
                rows = conn.execute(
                    "SELECT category, content, solution, created_at FROM memories "
                    "WHERE category='error' ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                results = [{"category": r[0], "content": r[1], "solution": r[2], "age_days": int((time.time()-r[3])/86400)} for r in rows]
            if not results:
                return "(无历史错误记录)", None
            lines = [f"## 历史错误回顾 ({len(results)} 条)"]
            for i, r in enumerate(results, 1):
                content = r.get("content", "")[:200]
                solution = r.get("solution", "")[:200]
                age = r.get("age_days", 0)
                lines.append(f"\n**[{i}] {age}天前**\n  {content}\n  FIX: {solution}")
            lines.append("\n⚠️ 读完要真的避免重蹈覆辙, 不要只是'看过了'。")
            return "\n".join(lines), None
        except Exception as e:
            return f"查询历史错误失败: {e}", None

    if scope == "patterns":
        if not HAS_MEMORY_INDEX:
            return "memory_index 未启用", None
        try:
            _midx = memory_index_get(WORKSPACE)
            conn = _midx._get_conn()
            rows = conn.execute(
                "SELECT SUBSTR(content, 1, 80) AS head, COUNT(*) AS cnt, "
                "GROUP_CONCAT(SUBSTR(solution, 1, 60), ' | ') AS sols "
                "FROM memories WHERE category='error' "
                "GROUP BY head HAVING cnt >= 2 ORDER BY cnt DESC LIMIT ?",
                (limit,)
            ).fetchall()
            if not rows:
                return "(未发现重复出错模式 - 历史错误都是独立的)", None
            lines = [f"## ⚠️ 重复出错模式 ({len(rows)} 种)"]
            for head, cnt, sols in rows:
                lines.append(f"\n**{cnt}次: {head}**\n  修复: {sols}")
            lines.append("\n这些是反复踩的坑, 下次遇到类似情况直接应用已知修复方案, 不要再盲试。")
            return "\n".join(lines), None
        except Exception as e:
            return f"模式分析失败: {e}", None

    if scope == "summary":
        return (
            "请根据本 session 的对话历史, 提炼以下内容并通过 save_memory 写入:\n"
            "1. 完成的关键任务 (section='Completed Work')\n"
            "2. 遇到的错误及解决方法 (section='Errors & Corrections', 格式: ERROR: ... | ROOT CAUSE: ... | FIX: ...)\n"
            "3. 学到的教训 (section='Lessons Learned', 格式: 'LESSON: <核心要点>')\n"
            "只提炼真正有价值的(可跨 session 复用的), 不要记流水账。"
        ), None

    return f"ERROR: unknown scope '{scope}'. 可用: errors, summary, skill_usage, patterns", None


@register("create_skill")
async def h_create_skill(sid: str, args: dict) -> tuple[str, Any]:
    skill_name = args.get("name", "").strip()
    description = args.get("description", "").strip()
    content = args.get("content") or "".strip()
    activate = bool(args.get("activate", False))
    if not skill_name or not content:
        return "ERROR: name and content are required", None
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', skill_name):
        return "ERROR: skill_name 只能包含小写字母/数字/连字符/下划线, 且以字母或数字开头", None
    target_parent = SKILLS_DIR if activate else (SKILLS_DIR / "drafts")
    target_parent.mkdir(parents=True, exist_ok=True)
    skill_dir = target_parent / skill_name
    if skill_dir.exists() and not args.get("overwrite", False):
        return (f"ERROR: skill '{skill_name}' already exists at {skill_dir}. "
                f"Use overwrite=true to replace, or pick a different name."), None
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"---\nname: {skill_name}\ndescription: {description}\n---\n\n{content}")
    if activate:
        _build_skill_index()
        return (f"Skill '{skill_name}' 已激活 -> {skill_md}\n"
                f"(skill index 已热更新, 当前共 {len(_SKILL_INDEX)} 个)"), None
    else:
        return (f"Skill draft 已创建 -> {skill_md}\n"
                f"⚠️ 草稿状态，不会被自动加载。用户审核后执行:\n"
                f"  mv {skill_dir} {SKILLS_DIR}/{skill_name}\n"
                f"并重启服务或调 /skills/rebuild-index 激活。"), None
