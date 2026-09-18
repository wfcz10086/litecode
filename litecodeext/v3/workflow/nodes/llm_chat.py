"""llm.chat node — call any registered LLM provider from config.json."""
from __future__ import annotations

from ...config_bridge import build_provider
from ...providers.base import ThinkingSpec, UnifiedMessage, UnifiedRequest
from ..registry import node_registry
from ..spec import NodeContext


@node_registry.register(
    "llm.chat",
    inputs={"prompt": "str", "system": "str?", "history": "list?"},
    outputs={"text": "str", "thinking": "str"},
    description="One-shot LLM call. config.model = model id from config.json.",
)
async def llm_chat(ctx: NodeContext) -> dict:
    model_id = ctx.config.get("model")
    if not model_id:
        raise ValueError("llm.chat requires config.model (model id from config.json)")

    prompt = ctx.inputs.get("prompt") or ""
    system = ctx.inputs.get("system") or ctx.config.get("system")
    history = ctx.inputs.get("history") or []

    messages: list[UnifiedMessage] = []
    if system:
        messages.append(UnifiedMessage(role="system", content=str(system)))
    for h in history:
        if isinstance(h, dict) and "role" in h:
            messages.append(UnifiedMessage(role=h["role"], content=h.get("content", "")))
    messages.append(UnifiedMessage(role="user", content=str(prompt)))

    effort = ctx.config.get("effort", "off")
    budget = ctx.config.get("thinking_budget")
    thinking = ThinkingSpec(effort=effort, budget_tokens=budget) if effort else None

    provider = build_provider(model_id)
    req = UnifiedRequest(
        model=model_id,
        messages=messages,
        thinking=thinking,
        temperature=ctx.config.get("temperature"),
        max_tokens=ctx.config.get("max_tokens"),
        stream=True,
    )
    text = ""
    thinking_text = ""
    async for chunk in provider.stream(req):
        if chunk.kind == "content" and chunk.delta:
            text += chunk.delta
        elif chunk.kind == "thinking" and chunk.delta:
            thinking_text += chunk.delta
        elif chunk.kind == "error":
            raise RuntimeError(chunk.delta or "llm.chat failed")
        elif chunk.kind == "done":
            break
    return {"text": text.strip(), "thinking": thinking_text.strip(), "out": text.strip()}
