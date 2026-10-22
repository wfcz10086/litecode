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
    """[2026-10-22] 草稿写到 WORKSPACE/skills/_drafts/ (lib/skill_registry.py, 升级不丢), 用户在网页
    技能面板审核启用。config.json skills.auto_create: draft (默认) / off (不让模型建技能) /
    activate (直接启用, 仍需安全扫描通过, 不通过退回草稿)。activate=true (用户明确要求) 同样要过扫描。"""
    from lib import skill_registry as reg
    from lib.config import CFG
    skill_name = str(args.get("name", "") or "").strip()
    description = " ".join(str(args.get("description", "") or "").split())
    content = str(args.get("content") or "").strip()
    mode = str(((CFG.get("skills") or {}).get("auto_create")) or "draft").strip().lower()
    activate = bool(args.get("activate", False)) or mode == "activate"
    if mode == "off":
        return "ERROR: 用户已关闭模型自动创建技能 (config.json skills.auto_create=off), 请告诉用户可在技能面板手动新建", None
    if not skill_name or not content:
        return "ERROR: name and content are required", None
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', skill_name):
        return "ERROR: skill_name 只能包含小写字母/数字/连字符/下划线, 且以字母或数字开头", None
    if (SKILLS_DIR / skill_name / "SKILL.md").is_file():
        return f"ERROR: '{skill_name}' 是内置技能名, 换一个名字", None
    exists = reg.draft_path(skill_name) or (reg.home() / skill_name / "SKILL.md").is_file()
    if exists and not args.get("overwrite", False):
        return (f"ERROR: skill '{skill_name}' already exists. "
                f"Use overwrite=true to replace, or pick a different name."), None
    desc_line = description.replace('"', "'")
    text = f"---\nname: {skill_name}\ndescription: \"{desc_line}\"\n---\n\n{content}\n"
    if activate:
        from lib import skill_install
        try:
            out = skill_install.install(skill_install.stage_skill_md(text, skill_name, source="created"))
        except skill_install.InstallError as e:
            out = {"installed": [], "skipped": [{"name": skill_name, "reason": str(e)}]}
        if out.get("installed"):
            _build_skill_index()
            return (f"Skill '{skill_name}' 已启用 (安全扫描通过), 当前共 {len(_SKILL_INDEX)} 个技能。"
                    f"用户可在网页「🧩 技能」面板查看/停用。"), None
        why = "; ".join(s.get("reason", "") for s in out.get("skipped") or []) or "安全扫描有需要人工确认的项"
        d = reg.write_draft(skill_name, text)
        return (f"Skill '{skill_name}' 未能直接启用 ({why}), 已存为草稿 -> {d / 'SKILL.md'}\n"
                f"请告诉用户: 到网页「🧩 技能」面板的草稿区审核后启用。"), None
    d = reg.write_draft(skill_name, text)
    return (f"Skill draft 已创建 -> {d / 'SKILL.md'}\n"
            f"⚠️ 草稿状态，不会被自动加载。请告诉用户: 到网页「🧩 技能」面板的草稿区审核 (可编辑/试运行), "
            f"确认后点「启用」。"), None
