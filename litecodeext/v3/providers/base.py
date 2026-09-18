"""Provider abstractions: UnifiedRequest / UnifiedChunk / ThinkingSpec / Protocols.

Every LLM or Vision provider implements these dataclasses/protocols.
No provider-specific field leaks above this layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

# ---------- Thinking ----------

EffortLevel = Literal["off", "low", "medium", "high", "auto"]


@dataclass
class ThinkingSpec:
    """Unified 'thinking budget' spec. Each provider translates internally."""
    effort: EffortLevel = "off"
    budget_tokens: int | None = None
    max_time_ms: int | None = None


# Default token budgets by effort (providers may override).
DEFAULT_BUDGETS: dict[EffortLevel, int] = {
    "off": 0,
    "low": 2000,
    "medium": 8000,
    "high": 32000,
    "auto": 4000,
}


# ---------- Messages ----------

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class UnifiedMessage:
    role: Role
    content: str | list[dict] = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class UnifiedToolSpec:
    name: str
    description: str
    parameters: dict


# ---------- Request ----------

@dataclass
class UnifiedRequest:
    messages: list[UnifiedMessage]
    model: str
    tools: list[UnifiedToolSpec] | None = None
    thinking: ThinkingSpec | None = None
    stream: bool = True
    stop: list[str] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict = field(default_factory=dict)


# ---------- Chunks (unified SSE-like event stream) ----------

ChunkKind = Literal[
    "thinking",
    "content",
    "tool_call",
    "tool_result",
    "vision",
    "usage",
    "phase",
    "error",
    "done",
]


@dataclass
class UnifiedChunk:
    kind: ChunkKind
    delta: str | None = None
    payload: dict | None = None
    round: int | None = None

    @classmethod
    def content(cls, delta: str, round: int | None = None) -> "UnifiedChunk":
        return cls(kind="content", delta=delta, round=round)

    @classmethod
    def thinking(cls, delta: str, round: int | None = None) -> "UnifiedChunk":
        return cls(kind="thinking", delta=delta, round=round)

    @classmethod
    def usage(cls, payload: dict) -> "UnifiedChunk":
        return cls(kind="usage", payload=payload)

    @classmethod
    def tool_call(cls, payload: dict, round: int | None = None) -> "UnifiedChunk":
        return cls(kind="tool_call", payload=payload, round=round)

    @classmethod
    def error(cls, message: str, payload: dict | None = None) -> "UnifiedChunk":
        return cls(kind="error", delta=message, payload=payload)

    @classmethod
    def done(cls, payload: dict | None = None) -> "UnifiedChunk":
        return cls(kind="done", payload=payload)


# ---------- Capabilities ----------

@dataclass
class ProviderCapabilities:
    name: str
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_thinking: bool = False
    thinking_controllable: bool = False  # can we control budget/effort?
    max_context: int = 8192
    models: list[str] = field(default_factory=list)


# ---------- Protocols ----------

@runtime_checkable
class LLMProvider(Protocol):
    """Every LLM provider (Kimi/DeepSeek/Ollama/...) implements this.

    Registered via @llm_registry.register("<name>").
    """

    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    async def stream(self, req: UnifiedRequest) -> AsyncIterator[UnifiedChunk]:
        """Yield UnifiedChunk events. Must always end with kind='done' or 'error'."""
        ...


@runtime_checkable
class VisionProvider(Protocol):
    """OCR / image understanding (PaddleOCR / Qwen-VL / Kimi-VL / ...)."""

    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    async def ocr(self, image: bytes | str, **opts: Any) -> dict:
        """Return {'text': str, 'blocks': list[dict], 'confidence': float}."""
        ...

    async def describe(self, image: bytes | str, prompt: str = "", **opts: Any) -> str: ...
