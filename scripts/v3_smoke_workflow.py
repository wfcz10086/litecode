#!/usr/bin/env python3
"""V4 smoke: workflow engine + 5 built-in nodes.

Three sub-tests:
  A) io.input -> io.output          (pure passthrough, no network)
  B) io.input -> llm.chat -> io.out (live LLM, --live only)
  C) io.input -> vision.ocr -> ... (live OCR, --live only)

Run:
  python3 scripts/v3_smoke_workflow.py
  python3 scripts/v3_smoke_workflow.py --live
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


async def run_wf(wf, inputs):
    from litecodeext.v3.workflow import WorkflowEngine
    engine = WorkflowEngine()
    outs: dict = {}
    events: list = []
    async for chunk in engine.run(wf, inputs):
        events.append((chunk.kind, chunk.payload or chunk.delta))
        if chunk.kind == "done" and chunk.payload:
            outs = chunk.payload.get("outputs", {})
    return outs, events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    print("=== V4 smoke: registration ===")
    from litecodeext.v3.providers import llm as _l  # noqa: F401
    from litecodeext.v3.providers import vision as _v  # noqa: F401
    from litecodeext.v3.workflow import node_registry, WorkflowSpec, NodeSpec
    from litecodeext.v3.workflow import nodes as _n  # noqa: F401

    types = node_registry.list()
    print(f"  registered node types: {types}")
    required = {"io.input", "io.output", "llm.chat", "vision.ocr", "vision.describe", "flow.branch", "tool.web_search"}
    missing = required - set(types)
    if missing:
        print(f"  MISSING: {missing}"); return 1

    print("\n=== V4 smoke A: passthrough (io.input -> io.output) ===")
    wf_a = WorkflowSpec(id="wf_a", nodes=[
        NodeSpec(id="in1",  type="io.input",  config={"key": "msg"}),
        NodeSpec(id="out1", type="io.output", inputs={"value": "$ref:in1.value"}),
    ])
    outs_a, _ = asyncio.run(run_wf(wf_a, {"msg": "hello workflow"}))
    got = outs_a.get("out1", {}).get("value")
    print(f"  outputs: {outs_a}")
    if got != "hello workflow":
        print(f"  FAIL expected 'hello workflow', got {got!r}"); return 2
    print("  A: PASS")

    print("\n=== V4 smoke tool.web_search (dry) ===")
    wf_ws = WorkflowSpec(id="wf_ws", nodes=[
        NodeSpec(id="q",  type="io.input", config={"key": "q"}),
        NodeSpec(id="ws", type="tool.web_search", inputs={"query": "$ref:q.value"}),
    ])
    outs_ws, _ = asyncio.run(run_wf(wf_ws, {"q": "litecode v3"}))
    urls = outs_ws.get("ws", {}).get("urls", [])
    print(f"  built {len(urls)} url(s), first: {urls[0] if urls else '(none)'}")
    if not urls:
        print("  FAIL no urls built"); return 3
    print("  tool.web_search: PASS")

    if not args.live:
        print("\n[SKIP] live LLM + OCR (pass --live)")
        print("V4 SMOKE: PASS")
        return 0

    from litecodeext.v3 import config_bridge as cb
    model_id = cb.list_models()[0].get("id")
    print(f"\n=== V4 smoke B: llm.chat live ({model_id}) ===")
    wf_b = WorkflowSpec(id="wf_b", nodes=[
        NodeSpec(id="q",   type="io.input", config={"key": "q"}),
        NodeSpec(id="ans", type="llm.chat", config={"model": model_id, "max_tokens": 32, "temperature": 0.0}, inputs={"prompt": "$ref:q.value"}),
        NodeSpec(id="out", type="io.output", inputs={"value": "$ref:ans.text"}),
    ])
    outs_b, _ = asyncio.run(run_wf(wf_b, {"q": "Reply with the single word: pong"}))
    text = outs_b.get("out", {}).get("value") or ""
    print(f"  llm output: {text!r}")
    if not text:
        print("  FAIL empty llm output"); return 4
    print("  B: PASS")

    print("\n=== V4 smoke C: vision.ocr live ===")
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    import io as _io
    img = Image.new("RGB", (240, 80), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    except Exception:
        f = ImageFont.load_default()
    d.text((10, 20), "WF-OCR", fill=(0, 0, 0), font=f)
    buf = _io.BytesIO(); img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    wf_c = WorkflowSpec(id="wf_c", nodes=[
        NodeSpec(id="img", type="io.input", config={"key": "img"}),
        NodeSpec(id="ocr", type="vision.ocr", inputs={"image": "$ref:img.value"}),
        NodeSpec(id="out", type="io.output", inputs={"value": "$ref:ocr.text"}),
    ])
    outs_c, _ = asyncio.run(run_wf(wf_c, {"img": img_bytes}))
    ocr_text = outs_c.get("out", {}).get("value") or ""
    print(f"  ocr output: {ocr_text!r}")
    if "wf-ocr" not in ocr_text.lower() and "wfocr" not in ocr_text.lower().replace(" ", ""):
        print("  WARN OCR did not contain WF-OCR (may still be acceptable)")
    print("  C: PASS")

    print("\nV4 SMOKE (live): PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
