"""DeepSeek provider (V3/V4/reasoner series).

Speaks OpenAI /v1/chat/completions with two P0 quirks:
- thinking is exposed via `delta.reasoning_content` (already handled by base)
- deep-thinking is selected by model id (`deepseek-reasoner` / `deepseek-v4-*`);
  ThinkingSpec.effort has no direct API knob, so we only decide whether to
  advertise supports_thinking.
"""
from __future__ import annotations

from ..base import ThinkingSpec
from ..registry import llm_registry
from .openai_compat import OpenAICompatProvider


@llm_registry.register("deepseek")
class DeepSeekProvider(OpenAICompatProvider):
    name = "deepseek"

    def translate_thinking(self, spec: ThinkingSpec | None) -> dict:
        # DeepSeek chat API accepts no explicit budget knob; some gateway
        # forks (e.g. gether) accept `enable_thinking` / `thinking_budget`
        # inherited from Qwen3 spec. Pass through if the config has them.
        out: dict = {}
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
        if spec.budget_tokens:
            out["thinking_budget"] = int(spec.budget_tokens)
        elif self._thinking_budget:
            out["thinking_budget"] = int(self._thinking_budget)
        return out
