"""智谱 GLM provider (GLM-4.5 / GLM-5 hybrid reasoning family).

Official OpenAI-compatible endpoint: https://open.bigmodel.cn/api/paas/v4
Key format: `<id>.<secret>` — accepted directly as Bearer by v4 gateway.

Thinking control (GLM-4.5+):
- `enable_thinking: true|false`   — hybrid model toggle (default false in some SKUs)
- `reasoning_effort: none|low|high|max` — depth knob (v5 series)
Reasoning surfaces via `delta.reasoning_content` (base parser handles it).
"""
from __future__ import annotations

from ..base import ThinkingSpec
from ..registry import llm_registry
from .openai_compat import OpenAICompatProvider

_EFFORT_TO_GLM: dict[str, str] = {
    "off": "none",
    "low": "low",
    "medium": "high",   # GLM has no medium; medium -> high
    "high": "high",
    "auto": "high",
}


@llm_registry.register("glm")
class GlmProvider(OpenAICompatProvider):
    name = "glm"

    def translate_thinking(self, spec: ThinkingSpec | None) -> dict:
        out: dict = {}
        if spec is None:
            if self._thinking:
                out["enable_thinking"] = True
                if self._thinking_budget:
                    out["thinking_budget"] = int(self._thinking_budget)
            return out
        if spec.effort == "off":
            out["enable_thinking"] = False
            out["reasoning_effort"] = "none"
            return out
        out["enable_thinking"] = True
        out["reasoning_effort"] = _EFFORT_TO_GLM.get(spec.effort, "high")
        if spec.budget_tokens:
            out["thinking_budget"] = int(spec.budget_tokens)
        elif self._thinking_budget:
            out["thinking_budget"] = int(self._thinking_budget)
        return out

    def capabilities(self):
        caps = super().capabilities()
        caps.thinking_controllable = True  # GLM has effort + toggle
        return caps
