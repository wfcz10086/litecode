"""LLM provider implementations.

Each provider is a single file (<300 lines) registered via
@llm_registry.register("<name>"). Importing this package triggers
registration side-effects for the built-in ones.
"""
from . import openai_compat  # noqa: F401 registers 'openai_compat' + 'openai'
from . import deepseek       # noqa: F401
from . import qwen           # noqa: F401
from . import kimi           # noqa: F401
from . import glm            # noqa: F401
from . import ollama         # noqa: F401
