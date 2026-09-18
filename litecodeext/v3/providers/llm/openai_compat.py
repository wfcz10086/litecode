"""Generic OpenAI-compatible LLM provider.

Base class for Kimi / DeepSeek / Qwen (all speak OpenAI /v1/chat/completions).
Subclasses override tiny bits: id-prefix routing, thinking-field mapping,
vision message-part formatting.
"""
from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import aiohttp

from ..base import (
    ProviderCapabilities,
    ThinkingSpec,
    UnifiedChunk,
    UnifiedMessage,
    UnifiedRequest,
)
from ..registry import llm_registry


class OpenAICompatProvider:
    """Speaks OpenAI /v1/chat/completions. Streams SSE deltas -> UnifiedChunk.

    Subclass and override translate_thinking() / parse_delta() for provider-
    specific field names (e.g. DeepSeek's reasoning_content).
    """

    name: str = "openai_compat"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str = "",
        context_window: int = 8192,
        max_tokens: int | None = None,
        supports_vision: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        **_: Any,
    ) -> None:
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._ctx = int(context_window)
        self._max_tokens = max_tokens
        self._vision = bool(supports_vision)
        self._thinking = bool(enable_thinking)
        self._thinking_budget = thinking_budget

    # ---------- Capabilities ----------

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            supports_streaming=True,
            supports_tools=True,
            supports_vision=self._vision,
            supports_thinking=self._thinking,
            thinking_controllable=False,
            max_context=self._ctx,
            models=[self.model],
        )

    # ---------- Request translation ----------

    def _translate_message(self, m: UnifiedMessage) -> dict:
        d: dict[str, Any] = {"role": m.role}
        if m.name:
            d["name"] = m.name
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        d["content"] = m.content
        return d

    def translate_thinking(self, spec: ThinkingSpec | None) -> dict:
        """Provider-specific thinking params. Default: no-op (subclass overrides)."""
        return {}

    def build_payload(self, req: UnifiedRequest) -> dict:
        payload: dict[str, Any] = {
            "model": req.model or self.model,
            "messages": [self._translate_message(m) for m in req.messages],
            "stream": req.stream,
        }
        if req.tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.parameters,
                }} for t in req.tools
            ]
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        mt = req.max_tokens or self._max_tokens
        if mt:
            payload["max_tokens"] = mt
        if req.stop:
            payload["stop"] = req.stop
        payload.update(self.translate_thinking(req.thinking))
        payload.update(req.extra or {})
        return payload

    # ---------- Streaming ----------

    def parse_delta(self, delta: dict) -> list[UnifiedChunk]:
        """Convert one OpenAI SSE delta -> zero or more UnifiedChunk.

        Base impl handles `content` + `tool_calls` + `reasoning_content`
        (DeepSeek/Qwen style thinking). Override for provider-specific fields.
        """
        out: list[UnifiedChunk] = []
        if (rc := delta.get("reasoning_content")) is not None:
            out.append(UnifiedChunk.thinking(rc))
        if (thinking := delta.get("thinking")) is not None:  # some vendors
            out.append(UnifiedChunk.thinking(thinking if isinstance(thinking, str) else str(thinking)))
        if (c := delta.get("content")) is not None:
            out.append(UnifiedChunk.content(c))
        if tc := delta.get("tool_calls"):
            out.append(UnifiedChunk.tool_call({"tool_calls": tc}))
        return out

    async def stream(self, req: UnifiedRequest) -> AsyncIterator[UnifiedChunk]:
        url = f"{self.base_url}/chat/completions"
        payload = self.build_payload(req)
        payload["stream"] = True
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            # 拒绝 brotli — aiohttp 默认不带 brotli 解码, 部分厂商(Kimi K3)会主动 br 压缩.
            "Accept-Encoding": "gzip, deflate",
        }
        async with aiohttp.ClientSession() as sess:
            try:
                resp = None
                # [#68] 429 retry (Kimi K3 engine_overloaded / DashScope 限速): 指数退避 + jitter
                from lib.retry import sleep_backoff, LLM_POLICY
                for attempt in range(3):
                    resp = await sess.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=None, sock_read=300))
                    if resp.status != 429:
                        break
                    await resp.release()
                    await sleep_backoff(attempt, LLM_POLICY)
                assert resp is not None
                async with resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        yield UnifiedChunk.error(f"HTTP {resp.status}: {body[:500]}")
                        yield UnifiedChunk.done({"reason": "http_error"})
                        return
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            yield UnifiedChunk.done()
                            return
                        try:
                            evt = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for ch in evt.get("choices", []):
                            for chunk in self.parse_delta(ch.get("delta") or {}):
                                yield chunk
                        if usage := evt.get("usage"):
                            yield UnifiedChunk.usage(usage)
                    yield UnifiedChunk.done()
            except aiohttp.ClientError as e:
                yield UnifiedChunk.error(f"connection error: {e}")
                yield UnifiedChunk.done({"reason": "connection_error"})


# Register both aliases; subclasses (deepseek/kimi/qwen) register their own names.
llm_registry.register("openai_compat")(OpenAICompatProvider)
llm_registry.register("openai")(type("OpenAIProvider", (OpenAICompatProvider,), {"name": "openai"}))
