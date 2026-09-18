"""Qwen provider (Qwen3 / Qwen3.6-35B family, via vLLM or DashScope).

P0 quirks:
- thinking: `enable_thinking` (bool) + `thinking_budget` (int tokens)
  Some vLLM builds emit `reasoning_content` on delta; base parser handles it.
- vision: OpenAI-style image_url content parts (base handles as passthrough).
"""
from __future__ import annotations

from ..base import DEFAULT_BUDGETS, ThinkingSpec
from ..registry import llm_registry
from .openai_compat import OpenAICompatProvider


@llm_registry.register("qwen")
class QwenProvider(OpenAICompatProvider):
    name = "qwen"

    def translate_thinking(self, spec: ThinkingSpec | None) -> dict:
        out: dict = {}
        # Inherit config defaults when caller didn't specify a spec.
        if spec is None:
            if self._thinking:
                out["enable_thinking"] = True
                if self._thinking_budget:
                    out["thinking_budget"] = int(self._thinking_budget)
            return out
        if spec.effort == "off":
            out["enable_thinking"] = False
            return out
        out["enable_thinking"] = True
        budget = spec.budget_tokens or DEFAULT_BUDGETS.get(spec.effort)
        if budget:
            out["thinking_budget"] = int(budget)
        return out
