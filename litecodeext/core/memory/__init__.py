"""core/memory — 会话记忆子系统 (#26 拆分自单文件 memory.py 1396 行).

对外 API 与拆分前完全一致:
  MemoryManager, get_manager, get_all_sessions_with_memory,
  build_memory_context, maybe_compact,
  estimate_tokens,
  extract_user_info, extract_services,
  auto_update_user_md, auto_update_tools_md, register_dynamic_skill,
  smart_auto_memory, rule_based_memory_update, rule_capture_user_facts,
  SOFT_TOKEN_LIMIT, HARD_TOKEN_LIMIT, CHARS_PER_TOKEN, L1_MAX_LINES,
  MSG_SOFT_COUNT, MSG_HARD_COUNT, COMPACTION_PROMPT.

模块布局 (全部 ≤ 500 行硬顶):
  consts.py     — 配置 + 阈值 + 两个 prompt 模板
  iohelp.py     — estimate_tokens + _atomic_write + _validate_compressed
  manager.py    — MemoryManager 类
  session.py    — get_manager / build_memory_context / maybe_compact 注册表
  user_facts.py — extract_user_info + auto_update_*_md + register_dynamic_skill
  auto.py       — smart_auto_memory + rule_based_memory_update + rule_capture_user_facts
"""
from .consts import (
    CHARS_PER_TOKEN,
    COMPACTION_PROMPT,
    HARD_TOKEN_LIMIT,
    L1_MAX_LINES,
    MSG_HARD_COUNT,
    MSG_SOFT_COUNT,
    SOFT_TOKEN_LIMIT,
)
from .iohelp import _atomic_write, _validate_compressed, estimate_tokens
from .manager import MemoryManager
from .session import (
    build_memory_context,
    get_all_sessions_with_memory,
    get_manager,
    maybe_compact,
)
from .user_facts import (
    auto_update_tools_md,
    auto_update_user_md,
    extract_services,
    extract_user_info,
    register_dynamic_skill,
)
from .auto import (
    rule_based_memory_update,
    rule_capture_user_facts,
    smart_auto_memory,
)

__all__ = [
    "MemoryManager",
    "get_manager",
    "get_all_sessions_with_memory",
    "build_memory_context",
    "maybe_compact",
    "estimate_tokens",
    "extract_user_info",
    "extract_services",
    "auto_update_user_md",
    "auto_update_tools_md",
    "register_dynamic_skill",
    "smart_auto_memory",
    "rule_based_memory_update",
    "rule_capture_user_facts",
    "SOFT_TOKEN_LIMIT",
    "HARD_TOKEN_LIMIT",
    "CHARS_PER_TOKEN",
    "L1_MAX_LINES",
    "MSG_SOFT_COUNT",
    "MSG_HARD_COUNT",
    "COMPACTION_PROMPT",
]
