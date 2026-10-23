"""turn_sources.py — 注册到 lib/turn_context.py 的默认动态上下文来源。

每个来源一个小函数, 互不依赖; 想关掉某个来源 → turn_context.unregister(name),
想加新来源 → turn_context.register(name, fn, order)。
"""
from __future__ import annotations

import re

from . import turn_context as tc
from .config import CFG, WORKSPACE, HAS_MEMORY_INDEX, memory_index_get
from .prompt import _detect_auto_delegation, matched_skills_block, PROMPT_FILE
from .skill_turn import FORCED_LABEL
from .agent import failure_loop as _failure_loop
from .agent import subagent_registry as _sa_registry

try:
    from memory import recall as _recall, profile as _profile    # 容器 / 服务进程: core 在 sys.path
except ImportError:                                                # 宿主机测试
    from core.memory import recall as _recall, profile as _profile
try:
    import project_sessions as _proj
    from projects import ProjectStore as _ProjectStore
except ImportError:
    from core import project_sessions as _proj
    from core.projects import ProjectStore as _ProjectStore


def _agent_cfg() -> dict:
    return CFG.get("agent") or {}


def delegation(inp: tc.TurnInput) -> str:
    return _detect_auto_delegation(inp.user_message).strip()


_SKILL_MARK = re.compile(r"(?=\[Active Skill: )")


def skills(inp: tc.TurnInput) -> str:
    """匹配到的技能全文随本条消息进历史; 本会话历史里已给过的技能不再重复给。"""
    block = matched_skills_block(inp.user_message, external=PROMPT_FILE.exists())
    if not block:
        return ""
    seen = tc.seen_in(inp.history)
    head, *chunks = _SKILL_MARK.split(block)
    if block.startswith(FORCED_LABEL):   # 用户强制指定: 前文给过全文的只提醒一句, 不重复整篇
        chunks = [c if c[:c.find("]") + 1] not in seen else c[:c.find("]") + 1] + " (全文已在本会话前文给出)\n\n"
                  for c in chunks]
        return head + "".join(chunks)
    fresh = [c for c in chunks if c[:c.find("]") + 1] not in seen]
    return head + "".join(fresh) if fresh else ""


def providers_ref(inp: tc.TurnInput) -> str:
    """用户用 `@` 引用的能力接入: 每次引用都给完整明文 (key 可能刚改过), 见 lib/providers.py。"""
    from . import providers as _pv
    return _pv.refs_block(_pv.turn_refs())


def memory_index(inp: tc.TurnInput) -> str:
    if not HAS_MEMORY_INDEX:
        return ""
    # profile 类条目由 user_profile 来源给 (且是最新值), 这里不重复
    return memory_index_get(WORKSPACE).search_as_context(
        inp.user_message, limit=3, max_chars=1500, exclude_categories=("profile",)) or ""


def _mem_cfg() -> dict:
    return CFG.get("memory") or {}


def user_profile(inp: tc.TurnInput) -> str:
    """全局用户档案 (core/memory/profile.py): 常驻块内容变了才重给, 超预算的按相关度挑。"""
    if not _mem_cfg().get("user_profile", True):
        return ""
    return _profile.turn_block(WORKSPACE, inp.session_id, inp.user_message, seen=tc.seen_in(inp.history),
                               max_chars=int(_mem_cfg().get("profile_max_chars", 1200)),
                               k=int(_mem_cfg().get("profile_recall_k", 3)))


def memory_recall(inp: tc.TurnInput) -> str:
    cfg = _agent_cfg()
    if not cfg.get("memory_recall", True) or not inp.session_id:
        return ""
    entries = _recall.load_entries(WORKSPACE / "sessions" / inp.session_id)   # 同 MemoryManager.root
    if not entries:
        return ""
    seen = "\n".join(str(m.get("content") or "") for m in inp.history if isinstance(m, dict))
    seen += "\n" + tc.seen_in(inp.history)
    picked = _recall.select(
        inp.user_message, entries,
        k=int(cfg.get("memory_recall_k", 5)),
        pinned=tuple(cfg.get("memory_pinned_sections", ("Vault", "User Preferences"))),
        seen=seen,
        max_chars=int(cfg.get("memory_recall_max_chars", 1200)),
    )
    return _recall.render(picked)


def project(inp: tc.TurnInput) -> str:
    """会话属于某个项目时附上项目指令 / 工作目录 / 项目文档。
    内容不变就只给一次 (首行带指纹, 历史里有同一行即跳过); 用户改了指令, 下一条消息重新给。"""
    if not inp.session_id:
        return ""
    store = _ProjectStore(WORKSPACE)
    meta = _proj.project_of(store, inp.session_id)
    if not meta:
        return ""
    block = _proj.context_block(store, meta)
    if not block or block.split("\n", 1)[0] in tc.seen_in(inp.history):
        return ""
    return block


def enabled() -> bool:
    """config.json agent.turn_context=false → 回到旧行为 (动态块都进 system prompt)。"""
    return bool(_agent_cfg().get("turn_context", True))


def prepare(user_message: str, session_id: str, raw_history: list) -> tuple[str, str, list]:
    """服务端每轮入口: 返回 (history_anchor, 本轮上下文文本, 拼回发送形态的历史)。
    raw_history 必须是会话里原样存的历史 (带 KEY 字段), 去重与锚点都按它算。"""
    from .prompt_snapshot import history_anchor
    anchor = history_anchor(raw_history, user_message)
    ctx = tc.build(tc.TurnInput(user_message, session_id or "", raw_history)) if enabled() else ""
    return anchor, ctx, tc.materialize(raw_history)


tc.register("project", project, order=5)
tc.register("delegation", delegation, order=10)
tc.register("user_profile", user_profile, order=20)
tc.register("skills", skills, order=30)
tc.register("providers", providers_ref, order=35)
tc.register("memory_index", memory_index, order=40)
tc.register("memory_recall", memory_recall, order=50)
# 失败经验召回 (lib/agent/failure_loop.py): 相似度过阈值才给, 开关 agent.failure_memory.enabled
tc.register("failure_memory", _failure_loop.recall_source, order=60)
# 后台子代理已结束但结果未取走 → [SYSTEM-AGENTS] 简短提示 (lib/agent/subagent_registry.py)
tc.register("agents", _sa_registry.turn_source, order=70)
