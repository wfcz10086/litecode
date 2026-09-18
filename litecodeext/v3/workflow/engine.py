"""WorkflowEngine — topological execution with dependency resolution.

- Sync deps via PortRef -> outputs of already-executed nodes
- Yields UnifiedChunk-compatible progress events
- No parallelism in v0 (add in V8 once we need speed); keeps semantics obvious.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from ..providers.base import UnifiedChunk
from .registry import node_registry
from .spec import NodeContext, NodeSpec, PortRef, WorkflowSpec


class WorkflowExecutionError(Exception):
    def __init__(self, node_id: str, cause: BaseException) -> None:
        super().__init__(f"node {node_id!r} failed: {cause!r}")
        self.node_id = node_id
        self.cause = cause


def _topo_order(wf: WorkflowSpec) -> list[NodeSpec]:
    """Kahn's algorithm. Cycles raise ValueError."""
    deps: dict[str, set[str]] = {n.id: set() for n in wf.nodes}
    for n in wf.nodes:
        for v in n.resolved_inputs().values():
            if isinstance(v, PortRef):
                if v.node_id not in deps:
                    raise ValueError(f"node {n.id!r} depends on unknown node {v.node_id!r}")
                deps[n.id].add(v.node_id)
    order: list[NodeSpec] = []
    remaining = dict(deps)
    node_by_id = {n.id: n for n in wf.nodes}
    while remaining:
        ready = [nid for nid, ds in remaining.items() if not ds]
        if not ready:
            raise ValueError(f"cycle detected among nodes: {list(remaining)}")
        ready.sort()  # deterministic
        for nid in ready:
            order.append(node_by_id[nid])
            remaining.pop(nid)
            for ds in remaining.values():
                ds.discard(nid)
    return order


class WorkflowEngine:
    async def run(
        self,
        wf: WorkflowSpec,
        inputs: dict[str, Any] | None = None,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[UnifiedChunk]:
        wf_inputs = dict(inputs or {})
        order = _topo_order(wf)
        outputs: dict[str, dict[str, Any]] = {}

        yield UnifiedChunk(kind="phase", payload={"phase": "workflow_start", "workflow_id": wf.id, "nodes": len(order)})

        for node in order:
            if cancel_event is not None and cancel_event.is_set():
                yield UnifiedChunk(kind="phase", payload={"phase": "interrupted", "node": node.id})
                yield UnifiedChunk.done({"reason": "cancelled"})
                return

            resolved: dict[str, Any] = {}
            for k, v in node.resolved_inputs().items():
                if isinstance(v, PortRef):
                    up = outputs.get(v.node_id) or {}
                    if v.port not in up:
                        raise WorkflowExecutionError(
                            node.id,
                            KeyError(f"upstream {v.node_id}.{v.port} missing (produced: {list(up)})"),
                        )
                    resolved[k] = up[v.port]
                else:
                    resolved[k] = v

            ctx = NodeContext(
                workflow_id=wf.id,
                node_id=node.id,
                inputs=resolved,
                config=dict(node.config or {}),
                workflow_inputs=wf_inputs,
            )
            yield UnifiedChunk(kind="phase", payload={"phase": "node_start", "node": node.id, "type": node.type})

            try:
                handler = node_registry.get(node.type)
                out = await handler(ctx)
                if not isinstance(out, dict):
                    raise TypeError(f"node handler must return dict, got {type(out).__name__}")
                outputs[node.id] = out
            except Exception as e:
                yield UnifiedChunk.error(f"node {node.id!r} ({node.type}) failed: {e}", {"node": node.id})
                yield UnifiedChunk.done({"reason": "node_error", "node": node.id})
                raise WorkflowExecutionError(node.id, e) from e

            yield UnifiedChunk(kind="phase", payload={"phase": "node_done", "node": node.id, "output_keys": list(out.keys())})

        yield UnifiedChunk.done({"outputs": outputs})
