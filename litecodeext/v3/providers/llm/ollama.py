"""Ollama provider — local LLM runtime.

Uses Ollama's native /api/chat streaming endpoint (NDJSON, not SSE).
Modern Ollama (>=0.3) emits `message.thinking` for reasoning models.
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
    UnifiedRequest,
)
from ..registry import llm_registry


@llm_registry.register("ollama")
class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434",
        api_key: str = "",
        context_window: int = 8192,
        max_tokens: int | None = None,
        supports_vision: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        **_: Any,
    ) -> None:
        self.model = (model or "").removeprefix("ollama:")
        self.base_url = (base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self._ctx = int(context_window)
        self._max_tokens = max_tokens
        self._vision = bool(supports_vision)
        self._thinking = bool(enable_thinking)
        self._thinking_budget = thinking_budget

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            supports_streaming=True,
            supports_tools=True,
            supports_vision=self._vision,
            supports_thinking=self._thinking,
            thinking_controllable=True,  # think: true/false
            max_context=self._ctx,
            models=[self.model],
        )

    def _translate_thinking(self, spec: ThinkingSpec | None) -> bool:
        if spec is None:
            return self._thinking
        return spec.effort != "off"

    async def stream(self, req: UnifiedRequest) -> AsyncIterator[UnifiedChunk]:
        url = f"{self.base_url}/api/chat"
        messages = []
        for m in req.messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.content if isinstance(m.content, str) else ""}
            if isinstance(m.content, list):
                imgs: list[str] = []
                texts: list[str] = []
                for part in m.content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            texts.append(part.get("text", ""))
                        elif part.get("type") == "image_url":
                            u = part.get("image_url", {})
                            imgs.append(u.get("url", u) if isinstance(u, dict) else u)
                if imgs:
                    entry["images"] = imgs
                entry["content"] = "\n".join(texts)
            messages.append(entry)

        payload: dict[str, Any] = {
            "model": req.model or self.model,
            "messages": messages,
            "stream": True,
            "think": self._translate_thinking(req.thinking),
        }
        options: dict[str, Any] = {}
        if req.temperature is not None:
            options["temperature"] = req.temperature
        mt = req.max_tokens or self._max_tokens
        if mt:
            options["num_predict"] = mt
        if req.stop:
            options["stop"] = req.stop
        if options:
            payload["options"] = options
        if req.tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.parameters,
                }} for t in req.tools
            ]

        async with aiohttp.ClientSession() as sess:
            try:
                async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=None, sock_read=300)) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        yield UnifiedChunk.error(f"HTTP {resp.status}: {body[:500]}")
                        yield UnifiedChunk.done({"reason": "http_error"})
                        return
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        msg = evt.get("message") or {}
                        if (t := msg.get("thinking")):
                            yield UnifiedChunk.thinking(t)
                        if (c := msg.get("content")):
                            yield UnifiedChunk.content(c)
                        if tc := msg.get("tool_calls"):
                            yield UnifiedChunk.tool_call({"tool_calls": tc})
                        if evt.get("done"):
                            usage = {k: evt[k] for k in ("prompt_eval_count", "eval_count", "total_duration") if k in evt}
                            if usage:
                                yield UnifiedChunk.usage(usage)
                            yield UnifiedChunk.done()
                            return
            except aiohttp.ClientError as e:
                yield UnifiedChunk.error(f"connection error: {e}")
                yield UnifiedChunk.done({"reason": "connection_error"})
