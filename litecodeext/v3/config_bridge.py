"""Bridge: read legacy config.json `models[]` -> v3 provider instances.

Zero new config sections. Every model in `models[]` becomes a v3-callable
provider by inferring `provider` from `id` and merging config into overrides.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .providers.registry import llm_registry

_BASE = Path(__file__).resolve().parent.parent.parent  # /opt/litecode
_CFG_PATH = _BASE / "config.json"


def load_raw_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _CFG_PATH
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def infer_provider(model_cfg: dict) -> str:
    """Infer v3 provider name from a config.json model entry.

    Precedence:
    1. explicit `provider` field (opt-in escape hatch)
    2. id prefix heuristics
    3. openai_compat fallback
    """
    if p := model_cfg.get("provider"):
        return str(p)
    mid = (model_cfg.get("id") or "").lower()
    if mid.startswith("ollama:") or model_cfg.get("backend_type") == "ollama":
        return "ollama"
    if "deepseek" in mid:
        return "deepseek"
    if "qwen" in mid:
        return "qwen"
    if "moonshot" in mid or "kimi" in mid:
        return "kimi"
    if mid.startswith("glm") or "chatglm" in mid or "bigmodel" in mid:
        return "glm"
    if mid.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if mid.startswith("claude"):
        return "claude"
    return "openai_compat"


def list_models(cfg: dict | None = None) -> list[dict]:
    cfg = cfg if cfg is not None else load_raw_config()
    models = cfg.get("models") or []
    if not models and cfg.get("model"):
        models = [cfg["model"]]
    return list(models)


def get_model_cfg(model_id: str, cfg: dict | None = None) -> dict:
    for m in list_models(cfg):
        if m.get("id") == model_id:
            return dict(m)
    raise KeyError(f"model id '{model_id}' not found in config.json")


def build_provider(model_id: str, cfg: dict | None = None):
    """Instantiate the right v3 LLM provider for a given model id.

    The returned provider has its config baked in (base_url/api_key/model, etc).
    """
    m = get_model_cfg(model_id, cfg)
    prov_name = infer_provider(m)
    kwargs: dict[str, Any] = {
        "model": m.get("id"),
        "base_url": m.get("backend_url"),
        "api_key": m.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
        "context_window": m.get("context_window", 8192),
        "max_tokens": m.get("max_tokens"),
        "supports_vision": bool(m.get("supports_vision")),
        "enable_thinking": bool(m.get("enable_thinking")),
        "thinking_budget": m.get("thinking_budget"),
    }
    return llm_registry.get(prov_name, **kwargs)


def default_vision_model_id(cfg: dict | None = None) -> str | None:
    """Pick the default vision-capable model. Prefer `vision_fallback_id`."""
    cfg = cfg if cfg is not None else load_raw_config()
    if fid := cfg.get("vision_fallback_id"):
        return fid
    for m in list_models(cfg):
        if m.get("supports_vision"):
            return m.get("id")
    return None
