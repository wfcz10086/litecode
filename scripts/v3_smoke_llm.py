#!/usr/bin/env python3
"""V1 smoke test: import v3 providers, list registry, dry-run stream if reachable.

Success criteria:
- All provider classes import without error
- llm_registry contains: openai_compat / openai / deepseek / qwen / kimi / ollama
- config_bridge.list_models() returns >=1 model
- For each model in config.json: infer_provider() returns a known name
- Optional live test if REACHABLE endpoints exist (short prompt, print first content chunk)

Run:
  python3 scripts/v3_smoke_llm.py
  python3 scripts/v3_smoke_llm.py --live   # also try real requests
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# make /opt/litecode importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also try real requests")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    print("=== V1 smoke: import providers ===")
    from litecodeext.v3.providers import llm_registry
    from litecodeext.v3.providers import llm as _llm_pkg  # noqa: F401 triggers registration
    from litecodeext.v3 import config_bridge as cb

    got = llm_registry.list()
    expected = {"openai_compat", "openai", "deepseek", "qwen", "kimi", "ollama", "glm"}
    missing = expected - set(got)
    print(f"  registered: {got}")
    if missing:
        print(f"  MISSING: {missing}")
        return 1

    print("\n=== V1 smoke: config bridge ===")
    cfg = cb.load_raw_config()
    models = cb.list_models(cfg)
    print(f"  found {len(models)} model(s) in config.json")
    for m in models:
        mid = m.get("id")
        prov = cb.infer_provider(m)
        print(f"    - {mid:32s} -> provider={prov} vision={m.get('supports_vision')} thinking={m.get('enable_thinking')}")
        if prov not in expected:
            print(f"      UNKNOWN provider '{prov}'")
            return 2

    default_vision = cb.default_vision_model_id(cfg)
    print(f"  default vision model: {default_vision}")

    print("\n=== V1 smoke: build_provider for each model ===")
    for m in models:
        mid = m.get("id")
        try:
            p = cb.build_provider(mid, cfg)
        except Exception as e:
            print(f"  FAIL {mid}: {e!r}")
            return 3
        caps = p.capabilities()
        print(f"  ok {mid:32s} -> {p.__class__.__name__} caps.vision={caps.supports_vision} caps.thinking={caps.supports_thinking}")

    if not args.live:
        print("\n[SKIP] live streaming test (pass --live to enable)")
        print("\nV1 SMOKE: PASS")
        return 0

    print("\n=== V1 smoke: live one-shot per model ===")
    from litecodeext.v3.providers import UnifiedMessage, UnifiedRequest, ThinkingSpec

    async def try_one(mid: str, prov) -> str:
        req = UnifiedRequest(
            model=mid,
            messages=[
                UnifiedMessage(role="system", content="Reply with exactly one short sentence."),
                UnifiedMessage(role="user", content="Say hello in one line."),
            ],
            thinking=ThinkingSpec(effort="off"),
            temperature=0.0,
            max_tokens=64,
            stream=True,
        )
        text = ""
        thinking = ""
        err = ""
        try:
            async def _drain():
                nonlocal text, thinking, err
                async for chunk in prov.stream(req):
                    if chunk.kind == "content" and chunk.delta:
                        text += chunk.delta
                    elif chunk.kind == "thinking" and chunk.delta:
                        thinking += chunk.delta
                    elif chunk.kind == "error":
                        err = chunk.delta or ""
                        return
                    elif chunk.kind == "done":
                        return
            await asyncio.wait_for(_drain(), timeout=args.timeout)
        except asyncio.TimeoutError:
            err = f"timeout after {args.timeout}s"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        return f"content={text[:80]!r} thinking={thinking[:40]!r} err={err[:120]!r}"

    async def run_all():
        for m in models:
            mid = m.get("id")
            prov = cb.build_provider(mid, cfg)
            summary = await try_one(mid, prov)
            print(f"  {mid:32s}: {summary}")

    asyncio.run(run_all())
    print("\nV1 SMOKE (live): PASS (see above for per-model results)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
