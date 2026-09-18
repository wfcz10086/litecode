"""vision.ocr / vision.describe — image understanding via existing vision LLM."""
from __future__ import annotations

from ...providers.registry import vision_registry
from ..registry import node_registry
from ..spec import NodeContext


def _adapter(name: str = "llm_vision"):
    return vision_registry.get(name)


@node_registry.register(
    "vision.ocr",
    inputs={"image": "bytes|path|url"},
    outputs={"text": "str", "blocks": "list", "confidence": "float?"},
    description="OCR via the configured vision LLM (default: llm_vision -> Qwen3.6-35B).",
)
async def vision_ocr(ctx: NodeContext) -> dict:
    image = ctx.inputs.get("image")
    if image is None:
        raise ValueError("vision.ocr requires input 'image'")
    backend = ctx.config.get("backend", "llm_vision")
    r = await _adapter(backend).ocr(image, **ctx.config.get("opts", {}))
    return {"text": r.get("text", ""), "blocks": r.get("blocks", []), "confidence": r.get("confidence"), "out": r.get("text", "")}


@node_registry.register(
    "vision.describe",
    inputs={"image": "bytes|path|url", "prompt": "str?"},
    outputs={"text": "str"},
    description="Describe an image via the configured vision LLM.",
)
async def vision_describe(ctx: NodeContext) -> dict:
    image = ctx.inputs.get("image")
    if image is None:
        raise ValueError("vision.describe requires input 'image'")
    prompt = ctx.inputs.get("prompt") or ctx.config.get("prompt", "")
    backend = ctx.config.get("backend", "llm_vision")
    text = await _adapter(backend).describe(image, prompt=prompt, **ctx.config.get("opts", {}))
    return {"text": text, "out": text}
