"""flow.branch — if/else output routing."""
from __future__ import annotations

from ..registry import node_registry
from ..spec import NodeContext


@node_registry.register(
    "flow.branch",
    inputs={"cond": "any", "if_true": "any?", "if_false": "any?"},
    outputs={"value": "any", "taken": "str"},
    description="Emit 'if_true' or 'if_false' based on truthiness of 'cond'.",
)
async def flow_branch(ctx: NodeContext) -> dict:
    cond = ctx.inputs.get("cond")
    taken_true = bool(cond) if not isinstance(cond, str) else cond.strip().lower() not in ("", "0", "false", "no")
    value = ctx.inputs.get("if_true") if taken_true else ctx.inputs.get("if_false")
    return {"value": value, "taken": "true" if taken_true else "false", "out": value}
