#!/usr/bin/env python3
"""V5 smoke: SkillRegistry — scan + auto-register as skill.<name> nodes."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    from litecodeext.v3.workflow import node_registry
    from litecodeext.v3.workflow import nodes as _n  # noqa: F401
    from litecodeext.v3.skill_registry import skill_registry

    n = skill_registry.scan()
    print(f"=== V5 smoke: scanned {n} skills ===")
    if n == 0:
        print("  FAIL: no skills found"); return 1
    for s in skill_registry.list()[:5]:
        print(f"  - {s.name:30s} enabled={s.enabled} desc={s.description[:50]!r}")

    node_types = [t for t in node_registry.list() if t.startswith("skill.")]
    print(f"  registered as workflow nodes: {len(node_types)} (first: {node_types[:3]})")
    if len(node_types) != n:
        print(f"  FAIL: expected {n} node types, got {len(node_types)}"); return 2

    print("\n=== V5 smoke: enable/disable persistence ===")
    first = skill_registry.list()[0].name
    original = skill_registry.get(first).enabled
    skill_registry.set_enabled(first, not original)
    from litecodeext.v3.skill_registry import SkillRegistry
    reloaded = SkillRegistry()
    reloaded.scan()
    if reloaded.get(first).enabled == original:
        print(f"  FAIL: flag not persisted for {first}"); return 3
    print(f"  {first}: flipped {original}->{not original} and persisted OK")
    # restore
    skill_registry.set_enabled(first, original)

    if not args.live:
        print("\n[SKIP] live skill execution (pass --live)")
        print("V5 SMOKE: PASS")
        return 0

    print("\n=== V5 smoke: live skill node execution ===")
    import asyncio
    from litecodeext.v3.workflow import WorkflowSpec, NodeSpec, WorkflowEngine
    from litecodeext.v3 import config_bridge as cb

    model_id = cb.list_models()[0].get("id")
    skill_name = "chinese-code-review" if skill_registry.get("chinese-code-review") else skill_registry.list()[0].name
    wf = WorkflowSpec(id="wf_sk", nodes=[
        NodeSpec(id="q", type="io.input", config={"key": "q"}),
        NodeSpec(id="sk", type=f"skill.{skill_name}", config={"model": model_id, "max_tokens": 128, "temperature": 0.0}, inputs={"input": "$ref:q.value"}),
        NodeSpec(id="out", type="io.output", inputs={"value": "$ref:sk.text"}),
    ])
    engine = WorkflowEngine()

    async def go():
        outs = {}
        async for ch in engine.run(wf, {"q": "简短介绍 code review 的核心原则(50 字内)"}):
            if ch.kind == "done" and ch.payload:
                outs = ch.payload.get("outputs", {})
        return outs

    outs = asyncio.run(go())
    text = outs.get("out", {}).get("value") or ""
    print(f"  skill {skill_name!r} output: {text[:120]!r}")
    if not text:
        print("  FAIL: empty skill output"); return 4
    print("V5 SMOKE (live): PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
