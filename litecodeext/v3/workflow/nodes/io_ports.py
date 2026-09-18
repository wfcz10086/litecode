"""io.input / io.output — workflow boundary nodes."""
from __future__ import annotations

from ..registry import node_registry
from ..spec import NodeContext


@node_registry.register(
    "io.input",
    outputs={"value": "any"},
    description="Reads a value from workflow-level inputs by key (config.key).",
)
async def io_input(ctx: NodeContext) -> dict:
    key = ctx.config.get("key") or ctx.node_id
    default = ctx.config.get("default")
    return {"value": ctx.workflow_inputs.get(key, default), "out": ctx.workflow_inputs.get(key, default)}


@node_registry.register(
    "io.output",
    inputs={"value": "any"},
    outputs={"value": "any"},
    description="Terminal node that passes through its input (visible in run outputs).",
)
async def io_output(ctx: NodeContext) -> dict:
    v = ctx.inputs.get("value")
    return {"value": v, "out": v}
