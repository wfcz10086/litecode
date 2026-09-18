"""Vision provider implementations (OCR + image understanding).

Default: reuse the existing vision-capable LLM from config.json `models[]`
(currently Qwen3.6-35B via vLLM). No new endpoint / no new model deployed.
Optional: PaddleOCR local (only registered if paddleocr importable).
"""
from . import llm_vision_adapter  # noqa: F401 registers 'llm_vision'

try:  # PaddleOCR optional — only register if installed
    from . import paddleocr  # noqa: F401
except Exception:
    pass
