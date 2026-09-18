"""Kimi (Moonshot) provider — K2.x / K3 thinking family.

Endpoint: https://api.moonshot.cn/v1  (OpenAI compatible)
Reasoning surfaces via `delta.reasoning_content` (base parser handles it).

Thinking control differs between generations:
- **K3**       always reasoning, uses `reasoning_effort: low|medium|high|max`
- **K2.7-code** always reasoning, uses `thinking: {type: enabled}` — 官方拒绝 disabled;
               此模型还强制 temperature=1
- **K2.5/K2.6** 可开可关, uses `thinking: {type: enabled|disabled}`

We auto-select based on model id substring.
"""
from __future__ import annotations

from ..base import ThinkingSpec, UnifiedRequest
from ..registry import llm_registry
from .openai_compat import OpenAICompatProvider

_EFFORT_TO_KIMI: dict[str, str] = {
    "off": "low", "low": "low", "medium": "medium",
    "high": "high", "auto": "medium",
}


@llm_registry.register("kimi")
class KimiProvider(OpenAICompatProvider):
    name = "kimi"

    def _is_k3(self) -> bool:
        m = (self.model or "").lower()
        return m.startswith("kimi-k3") or "k3" in m

    def _is_k2(self) -> bool:
        m = (self.model or "").lower()
        return "k2" in m

    def _thinking_always_on(self) -> bool:
        # K2.7-code / K2.7-code-highspeed 官方规定思考常开; K3 也是常开.
        m = (self.model or "").lower()
        return self._is_k3() or "k2.7-code" in m

    def translate_thinking(self, spec: ThinkingSpec | None) -> dict:
        if spec is None or spec.effort == "off":
            if self._thinking_always_on():
                # 这些模型只能开 — 官方拒绝 disabled/未传
                return {"thinking": {"type": "enabled"}} if self._is_k2() else {}
            if self._is_k2():
                return {"thinking": {"type": "disabled"}}
            return {}
        if self._is_k3():
            return {"reasoning_effort": _EFFORT_TO_KIMI.get(spec.effort, "medium")}
        if self._is_k2():
            return {"thinking": {"type": "enabled"}}
        return {}

    def capabilities(self):
        caps = super().capabilities()
        caps.thinking_controllable = self._is_k3() or self._is_k2()
        return caps

    def build_payload(self, req: UnifiedRequest) -> dict:
        payload = super().build_payload(req)
        # K2.7-code / K3 强制 temperature=1，任何其它值都会 400.
        if self._thinking_always_on():
            payload["temperature"] = 1
        return payload
