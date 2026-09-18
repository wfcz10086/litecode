"""Simple type-safe provider registry.

Usage:
    @llm_registry.register("kimi")
    class KimiProvider: ...

    kimi = llm_registry.get("kimi", api_key="sk-...")
    for chunk in kimi.stream(req): ...
"""
from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Generic provider registry. One instance per kind (llm/vision/node/skill)."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._classes: dict[str, type[T]] = {}
        self._defaults: dict[str, dict] = {}

    def register(
        self,
        name: str,
        *,
        defaults: dict | None = None,
    ) -> Callable[[type[T]], type[T]]:
        """Decorator: register a provider class under a name."""

        def _wrap(cls: type[T]) -> type[T]:
            if name in self._classes:
                raise ValueError(f"{self._kind} provider '{name}' already registered")
            self._classes[name] = cls
            self._defaults[name] = defaults or {}
            setattr(cls, "name", name)
            return cls

        return _wrap

    def get(self, name: str, **overrides: Any) -> T:
        """Instantiate a registered provider with merged config (defaults + overrides)."""
        if name not in self._classes:
            avail = ", ".join(sorted(self._classes)) or "(none)"
            raise KeyError(f"{self._kind} provider '{name}' not found. Available: {avail}")
        cfg = {**self._defaults[name], **overrides}
        return self._classes[name](**cfg)

    def list(self) -> list[str]:
        return sorted(self._classes)

    def has(self, name: str) -> bool:
        return name in self._classes


llm_registry: Registry = Registry("llm")
vision_registry: Registry = Registry("vision")
