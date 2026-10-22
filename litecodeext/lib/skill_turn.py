"""lib/skill_turn.py — 本轮用到的技能: 强制指定 + 实际使用记录。

agent_stream 开头 begin(ctx.turn_skills, forced): 之后同一轮里 (含工具任务, ensure_future 会复制
context, 集合是同一个对象) 的自动匹配 / load_skill / 强制注入都经 note() 记进 ctx.turn_skills,
轮末作为 SSE `turn_skills` 帧发给 web_ui, 写进助手消息 meta.skills。
没有轮次上下文时 (CLI 单测 / 直接调 handler) note() 只计使用次数。
"""
from __future__ import annotations

from contextvars import ContextVar

MAX_FORCED = 5
_CUR: ContextVar = ContextVar("lc_turn_skills", default=None)   # (used: set, forced: tuple)


def begin(used: set, forced=()) -> None:
    names = []
    for x in forced or ():
        s = str(x or "").strip()
        if s and s not in names:
            names.append(s)
    _CUR.set((used, tuple(names[:MAX_FORCED])))


def forced() -> tuple:
    cur = _CUR.get()
    return cur[1] if cur else ()


def used() -> set:
    cur = _CUR.get()
    return cur[0] if cur else set()


def note(name: str) -> None:
    """记一次使用: 同一轮同一技能只计一次。"""
    if not name:
        return
    cur = _CUR.get()
    if cur is not None:
        if name in cur[0]:
            return
        cur[0].add(name)
    try:
        from . import skill_registry
        skill_registry.record_use(name)
    except Exception:
        pass


FORCED_LABEL = "## 用户为本轮指定的 Skills（必须按它们执行）\n"


def forced_block(names, paths: dict) -> tuple[str, list[str]]:
    """强制技能全文 (同 load_skill 的分节逻辑: 超长只给开头几节 + 续读提示)。返回 (文本, 实际注入的名字)。"""
    from .skill_body import read_source, render
    parts, got = [], []
    for n in names:
        text = read_source(paths.get(n))
        if text is None:
            continue
        body, _ = render(n, text)
        parts.append(f"[Active Skill: {n}]\n{body}")
        got.append(n)
    return ("\n\n".join(parts), got) if parts else ("", [])
