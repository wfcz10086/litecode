#!/usr/bin/env python3
"""V3 smoke test: Vision provider (OCR + describe).

- Verify llm_vision registered
- Auto-pick default vision model from config.json (Qwen3.6-35B)
- With --live: generate a tiny image with text and OCR it end-to-end
  using the existing Qwen3.6-35B endpoint (from config.json)
- With --paddle: also try PaddleOCR if installed

Run:
  python3 scripts/v3_smoke_ocr.py               # dry (registration only)
  python3 scripts/v3_smoke_ocr.py --live        # real OCR via existing Qwen3.6-35B
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _make_test_image() -> bytes:
    """Return a small PNG with text 'HELLO V3' rendered. Uses PIL if available,
    else a bundled sample if present, else raises."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception as e:
        raise RuntimeError(f"pillow required for image gen: {e}")
    img = Image.new("RGB", (320, 100), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
    except Exception:
        font = ImageFont.load_default()
    draw.text((20, 20), "HELLO V3", fill="black", font=font)
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--paddle", action="store_true")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    print("=== V3 smoke: registration ===")
    from litecodeext.v3.providers import vision_registry
    from litecodeext.v3.providers import llm as _llm_pkg  # noqa: F401
    from litecodeext.v3.providers import vision as _vis_pkg  # noqa: F401
    from litecodeext.v3 import config_bridge as cb

    got = vision_registry.list()
    print(f"  registered vision providers: {got}")
    if "llm_vision" not in got:
        print("  MISSING llm_vision"); return 1

    vid = cb.default_vision_model_id()
    print(f"  default vision model: {vid}")
    if not vid:
        print("  no vision-capable model configured — set supports_vision=true in config.json"); return 2

    if not args.live:
        print("\n[SKIP] live OCR (pass --live)")
        print("V3 SMOKE: PASS")
        return 0

    print("\n=== V3 smoke: live OCR (reusing existing Qwen3.6-35B) ===")
    img_bytes = _make_test_image()
    out_dir = _ROOT / "reports" / "v3_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke_ocr_input.png").write_bytes(img_bytes)
    print(f"  test image saved: reports/v3_smoke/smoke_ocr_input.png ({len(img_bytes)}B)")

    async def run():
        adapter = vision_registry.get("llm_vision")
        caps = adapter.capabilities()
        print(f"  adapter caps: vision={caps.supports_vision} model={caps.models}")

        r = await asyncio.wait_for(adapter.ocr(img_bytes), timeout=args.timeout)
        print(f"  OCR text:    {r['text']!r}")
        print(f"  OCR backend: {r.get('backend')}")

        d = await asyncio.wait_for(adapter.describe(img_bytes), timeout=args.timeout)
        print(f"  describe:    {d!r}")

        # loose check: "HELLO" appears (case-insensitive)
        ok = "hello" in (r["text"] or "").lower()
        return ok

    ok = asyncio.run(run())
    if args.paddle:
        try:
            paddle = vision_registry.get("paddleocr")
            r = asyncio.run(paddle.ocr(img_bytes))
            print(f"\n  PaddleOCR:   {r['text']!r} conf={r['confidence']:.2f}")
        except Exception as e:
            print(f"  paddleocr unavailable: {e}")

    print("\nV3 SMOKE (live):", "PASS" if ok else "FAIL (OCR text did not contain HELLO)")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
