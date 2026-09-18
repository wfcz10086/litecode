"""tool_dispatch handler registry (T-52d-2).

Handlers are registered via @register("tool_name") in each domain submodule.
tool_dispatch._execute_tool_impl looks up REGISTRY[name] before falling
through to its remaining if/elif branches for handlers not yet migrated.

Handler signature: async (sid, args) -> tuple[str, artifact|None]
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

Handler = Callable[..., Awaitable[tuple[str, Any]]]

REGISTRY: dict[str, Handler] = {}


def register(name: str):
    def _wrap(fn: Handler) -> Handler:
        REGISTRY[name] = fn
        return fn
    return _wrap


# side-effect: populate REGISTRY
from . import (  # noqa: E402,F401
    agent, bg_ops, browser, code_search, dag, error_search,
    file_io, git_ops, media, memory_ops, meta, remote, skills, snapshot_ops, ssh_hosts, symbol_edit, task_ops, timer,
)
