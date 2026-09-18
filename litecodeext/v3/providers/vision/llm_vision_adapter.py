"""VisionProvider backed by any vision-capable LLM already in config.json.

This is the *default* OCR/vision path. It **reuses the existing LLM provider**
configured in config.json `models[]` (any entry with supports_vision=true) —
in this project that is **Qwen3.6-35B** on the vLLM endpoint.

Zero new deployments. Zero new endpoints. Zero new keys.
The concrete model is chosen via `default_vision_model_id()` which reads
`vision_fallback_id` from config.json (currently "Qwen3.6-35B").
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from ..base import (
    ProviderCapabilities,
    UnifiedMessage,
    UnifiedRequest,
    ThinkingSpec,
)
from ..registry import vision_registry


def _encode_image(image: bytes | str) -> str:
    """Return an image_url string (data-URI for bytes / local paths, passthrough for URLs)."""
    if isinstance(image, bytes):
        b64 = base64.b64encode(image).decode("ascii")
        return f"data:image/png;base64,{b64}"
    if image.startswith(("http://", "https://", "data:")):
        return image
    p = Path(image)
    if p.exists():
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    return image  # let backend decide


@vision_registry.register("llm_vision")
class LlmVisionAdapter:
    """Wraps any vision-capable LLMProvider to expose ocr() / describe()."""

    name = "llm_vision"

    def __init__(self, llm: Any = None, model_id: str | None = None, **_: Any) -> None:
        if llm is None:
            from ...config_bridge import build_provider, default_vision_model_id
            mid = model_id or default_vision_model_id()
            if not mid:
                raise RuntimeError("no vision-capable model in config.json (need supports_vision=true)")
            llm = build_provider(mid)
            model_id = mid
        self._llm: Any = llm
        self._model_id: str | None = model_id

    def capabilities(self) -> ProviderCapabilities:
        caps = self._llm.capabilities()
        return ProviderCapabilities(
            name=self.name,
            supports_streaming=False,
            supports_tools=False,
            supports_vision=True,
            supports_thinking=caps.supports_thinking,
            thinking_controllable=caps.thinking_controllable,
            max_context=caps.max_context,
            models=[self._model_id] if self._model_id else [],
        )

    async def _one_shot(self, prompt: str, image: bytes | str, max_tokens: int = 2048) -> str:
        img_url = _encode_image(image)
        msg = UnifiedMessage(
            role="user",
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": img_url}},
            ],
        )
        req = UnifiedRequest(
            model=self._model_id or "",
            messages=[msg],
            thinking=ThinkingSpec(effort="off"),
            temperature=0.0,
            max_tokens=max_tokens,
            stream=True,
        )
        text = ""
        async for chunk in self._llm.stream(req):
            if chunk.kind == "content" and chunk.delta:
                text += chunk.delta
            elif chunk.kind == "error":
                raise RuntimeError(chunk.delta or "vision LLM error")
            elif chunk.kind == "done":
                break
        return text.strip()

    async def ocr(self, image: bytes | str, **opts: Any) -> dict:
        prompt = opts.get("prompt") or (
            "Extract ALL text from this image verbatim. Preserve line breaks. "
            "Do not describe, do not explain, only output the text."
        )
        text = await self._one_shot(prompt, image, max_tokens=opts.get("max_tokens", 4096))
        return {"text": text, "blocks": [], "confidence": None, "backend": self._model_id}

    async def describe(self, image: bytes | str, prompt: str = "", **opts: Any) -> str:
        prompt = prompt or "Describe this image concisely."
        return await self._one_shot(prompt, image, max_tokens=opts.get("max_tokens", 512))
