"""Workflow spec: NodeSpec / WorkflowSpec / PortRef / NodeContext.

Pure data + tiny runtime context object. No execution logic here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PortRef:
    """Reference to another node's output port. Serialized as '{node_id}.{port}'."""
    node_id: str
    port: str = "out"

    @classmethod
    def parse(cls, s: str) -> "PortRef":
        if "." in s:
            n, p = s.split(".", 1)
            return cls(node_id=n, port=p)
        return cls(node_id=s, port="out")

    def __str__(self) -> str:
        return f"{self.node_id}.{self.port}"


def _resolve_input(v: Any) -> Any:
    """If value is a string looking like '$ref:node.port', return PortRef; else literal."""
    if isinstance(v, str) and v.startswith("$ref:"):
        return PortRef.parse(v[5:])
    if isinstance(v, dict) and v.get("__ref__"):
        return PortRef.parse(v["__ref__"])
    return v


@dataclass
class NodeSpec:
    id: str
    type: str
    config: dict = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)

    def resolved_inputs(self) -> dict[str, Any]:
        return {k: _resolve_input(v) for k, v in self.inputs.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "NodeSpec":
        return cls(id=d["id"], type=d["type"], config=d.get("config", {}), inputs=d.get("inputs", {}))


@dataclass
class WorkflowSpec:
    id: str
    name: str = ""
    nodes: list[NodeSpec] = field(default_factory=list)
    version: int = 1
    meta: dict = field(default_factory=dict)

    def node(self, node_id: str) -> NodeSpec:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"node {node_id!r} not in workflow {self.id!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "WorkflowSpec":
        nodes = [NodeSpec.from_dict(n) for n in d.get("nodes", [])]
        return cls(id=d["id"], name=d.get("name", ""), nodes=nodes, version=d.get("version", 1), meta=d.get("meta", {}))

    @classmethod
    def load(cls, path: str | Path) -> "WorkflowSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "meta": self.meta,
            "nodes": [{"id": n.id, "type": n.type, "config": n.config, "inputs": n.inputs} for n in self.nodes],
        }


@dataclass
class NodeContext:
    """Runtime context passed to every node's execute(). Immutable per-node view."""
    workflow_id: str
    node_id: str
    inputs: dict[str, Any]         # already resolved (upstream outputs merged in)
    config: dict
    workflow_inputs: dict[str, Any]  # initial workflow-level inputs

    def get(self, key: str, default: Any = None) -> Any:
        return self.inputs.get(key, default)
