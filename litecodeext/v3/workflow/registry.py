"""Node type registry — every node kind (llm.chat / vision.ocr / io.input ...)
registers a factory here. Pluggable: adding a node = new file + one decorator.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .spec import NodeContext

# A node handler: (ctx) -> dict of outputs (must be awaitable)
NodeHandler = Callable[[NodeContext], Awaitable[dict[str, Any]]]


class NodeRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, NodeHandler] = {}
        self._meta: dict[str, dict] = {}

    def register(
        self,
        node_type: str,
        *,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
        description: str = "",
    ) -> Callable[[NodeHandler], NodeHandler]:
        def _wrap(fn: NodeHandler) -> NodeHandler:
            if node_type in self._handlers:
                raise ValueError(f"node type {node_type!r} already registered")
            self._handlers[node_type] = fn
            self._meta[node_type] = {
                "inputs": inputs or {},
                "outputs": outputs or {"out": "any"},
                "description": description,
            }
            return fn
        return _wrap

    def get(self, node_type: str) -> NodeHandler:
        if node_type not in self._handlers:
            avail = ", ".join(sorted(self._handlers)) or "(none)"
            raise KeyError(f"node type {node_type!r} unknown. Available: {avail}")
        return self._handlers[node_type]

    def list(self) -> list[str]:
        return sorted(self._handlers)

    def describe(self, node_type: str) -> dict:
        return dict(self._meta.get(node_type, {}))

    def all_meta(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._meta.items()}


node_registry = NodeRegistry()
