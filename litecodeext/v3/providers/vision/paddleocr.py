"""PaddleOCR local backup — only registered if `paddleocr` package installed.

Zero-dependency fallback for text-only OCR when no vision LLM is reachable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# hard import — this module is guarded by try/except in vision/__init__.py
from paddleocr import PaddleOCR  # type: ignore

from ..base import ProviderCapabilities
from ..registry import vision_registry


@vision_registry.register("paddleocr")
class PaddleOCRProvider:
    name = "paddleocr"

    def __init__(self, *, lang: str = "ch", gpu: bool = False, **_: Any) -> None:
        self._engine = PaddleOCR(use_angle_cls=True, lang=lang, use_gpu=gpu, show_log=False)
        self._lang = lang

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            supports_streaming=False,
            supports_tools=False,
            supports_vision=True,
            supports_thinking=False,
            thinking_controllable=False,
            max_context=0,
            models=[f"PP-OCRv4-{self._lang}"],
        )

    async def ocr(self, image: bytes | str, **opts: Any) -> dict:
        target: Any
        if isinstance(image, bytes):
            import tempfile
            fp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            fp.write(image)
            fp.close()
            target = fp.name
        else:
            target = str(Path(image))
        result = self._engine.ocr(target, cls=True)
        blocks: list[dict] = []
        text_lines: list[str] = []
        confs: list[float] = []
        for page in result or []:
            for row in page or []:
                bbox, (txt, conf) = row
                blocks.append({"bbox": bbox, "text": txt, "confidence": float(conf)})
                text_lines.append(txt)
                confs.append(float(conf))
        avg_conf = sum(confs) / len(confs) if confs else 0.0
        return {"text": "\n".join(text_lines), "blocks": blocks, "confidence": avg_conf, "backend": "paddleocr"}

    async def describe(self, image: bytes | str, prompt: str = "", **opts: Any) -> str:
        r = await self.ocr(image, **opts)
        return r["text"]
