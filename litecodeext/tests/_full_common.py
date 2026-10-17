#!/usr/bin/env python3
"""test_full 拆分后的公共部分: 配置 / 颜色 / sys.path / 离线测试记录器 _make_tester。"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip3 install requests --break-system-packages")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────
# [layout] 测试文件位于 litecodeext/tests/, 源码在父目录 litecodeext/
BASE = Path(__file__).resolve().parent.parent
# [layout v2] core/ 放了 15 个核心模块, 加到 sys.path 让扁平 import 继续工作
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

def _load_cfg() -> dict:
    p = BASE / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}

_CFG = _load_cfg()
_SRV = _CFG.get("server", {})
DEFAULT_URL   = f"http://127.0.0.1:{_SRV.get('port', 18789)}"
DEFAULT_TOKEN = _SRV.get("token", "CHANGE_ME_TOKEN")
DEFAULT_MODEL = _CFG.get("model", {}).get("id", "qwen3.6")

LOGS_DIR = Path(_CFG.get("paths", {}).get("logs_dir", "/tmp/litecode_workspace/logs"))
LOGS_DIR.mkdir(parents=True, exist_ok=True)
REPORT_FILE = LOGS_DIR / "test_full_report.md"
TRACE_LOG   = LOGS_DIR / "iteration_full.log"

# ── Colors ────────────────────────────────────────────────────
_T = sys.stdout.isatty()
G  = "\033[32m" if _T else ""
R  = "\033[31m" if _T else ""
Y  = "\033[33m" if _T else ""
C  = "\033[36m" if _T else ""
B  = "\033[1m"  if _T else ""
D  = "\033[2m"  if _T else ""
NC = "\033[0m"  if _T else ""


# ══════════════════════════════════════════════════════════════
# v1.0 OFFLINE UNIT TESTS (无需 server, 直接跑)
# ══════════════════════════════════════════════════════════════

def _make_tester(results: list):
    """构造绑定到某个 results 列表的 _test 记录闭包 (P2-1 拆分抽出, 机制/行为不变)。"""
    def _test(name, fn):
        t0 = time.time()
        try:
            fn()
            elapsed = time.time() - t0
            results.append({"name": name, "pass": True, "elapsed": round(elapsed, 3), "error": ""})
            print(f"  {G}✅{NC} {name} ({elapsed:.3f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            results.append({"name": name, "pass": False, "elapsed": round(elapsed, 3), "error": str(e)})
            print(f"  {R}❌{NC} {name}: {e}")
    return _test
