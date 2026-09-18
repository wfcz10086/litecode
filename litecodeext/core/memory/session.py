"""memory/session.py — 全局 MemoryManager 注册表 + agent.py 兼容接口."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .manager import MemoryManager

_reg: dict[str, MemoryManager] = {}
_reg_lock = threading.Lock()


def get_manager(
    workspace: Path,
    session_id: str,
    vllm_url: str,
    model_id: str,
    api_key: str = "EMPTY",
    context_window: int = 65000,
) -> MemoryManager:
    with _reg_lock:
        if session_id not in _reg:
            _reg[session_id] = MemoryManager(
                workspace=workspace, session_id=session_id,
                vllm_url=vllm_url, model_id=model_id,
                api_key=api_key, context_window=context_window,
            )
        return _reg[session_id]


def get_all_sessions_with_memory(workspace: Path) -> list:
    d = workspace / "sessions"
    if not d.exists():
        return []
    return [x.name for x in d.iterdir() if x.is_dir() and (x / "MEMORY.md").exists()]


def build_memory_context(workspace: Path, session_id: str = "default") -> tuple:
    """agent.py 兼容接口: 返回 (memory_text, MemoryManager)."""
    mgr = get_manager(
        workspace=workspace, session_id=session_id,
        vllm_url=os.environ.get("VLLM_URL", "http://127.0.0.1:8001/v1"),
        model_id=os.environ.get("MODEL_ID", "openclaw"),
        api_key=os.environ.get("API_KEY", "EMPTY"),
        context_window=int(os.environ.get("CONTEXT_WINDOW", "65000")),
    )
    text = mgr.load_for_prompt()
    return text, mgr


def maybe_compact(mgr: MemoryManager, messages: list) -> list:
    """agent.py 兼容接口: 检查是否需要压缩, 返回 messages (可能被裁剪)."""
    mgr.check_and_compact(messages, _force_async=True)
    return messages
