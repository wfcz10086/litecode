#!/usr/bin/env python3
"""V3 系统自检 — 一次跑完所有 smoke, 汇总 pass/fail 矩阵.

由 skills/system-selfcheck 触发, 也可以命令行直接跑:
    python3 scripts/v3_selfcheck.py
    python3 scripts/v3_selfcheck.py --only=gateway,official
    python3 scripts/v3_selfcheck.py --skip=vision

任一子系统失败退 1, 全绿退 0.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

SUBSYSTEMS: list[tuple[str, str]] = [
    ("providers", "scripts/v3_smoke_llm.py"),
    ("gateway",   "scripts/v3_smoke_gateway.py"),
    ("official",  "scripts/v3_smoke_official.py"),
    ("workflow",  "scripts/v3_smoke_workflow.py"),
    ("skills",    "scripts/v3_smoke_skills.py"),
    ("vision",    "scripts/v3_smoke_ocr.py"),
]


def _run(name: str, script: str, timeout: int) -> tuple[bool, float, str]:
    path = _ROOT / script
    if not path.exists():
        return False, 0.0, f"MISSING SCRIPT: {script}"
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(path)],
            cwd=str(_ROOT), capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, timeout, f"TIMEOUT after {timeout}s"
    dur = time.perf_counter() - t0
    ok = proc.returncode == 0
    tail = (proc.stdout or "")[-400:] + (("\n[stderr]\n" + proc.stderr[-200:]) if proc.stderr else "")
    return ok, dur, tail.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑列表 (逗号分隔)")
    ap.add_argument("--skip", default="", help="跳过列表 (逗号分隔)")
    ap.add_argument("--timeout", type=int, default=300, help="单子系统超时秒")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    print("=== System SelfCheck ===")
    results: list[tuple[str, bool, float, str]] = []
    for name, script in SUBSYSTEMS:
        if only and name not in only:
            continue
        if name in skip:
            print(f"  SKIP {name}")
            continue
        print(f"  ... {name} ({script})")
        ok, dur, tail = _run(name, script, args.timeout)
        results.append((name, ok, dur, tail))
        status = "OK  " if ok else "FAIL"
        print(f"  {status} {name:12s} {dur:5.1f}s")
        if not ok:
            print(f"    -- tail --\n{tail}\n    ----------")

    total = len(results)
    passed = sum(1 for _, ok, _, _ in results if ok)
    print(f"\n=== {passed}/{total} PASS ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
