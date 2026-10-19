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
DEFAULT_TOKEN = _SRV.get("token", "")
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
# 行为化测试辅助 (替代"读源码 + 子串匹配"的断言)
# ══════════════════════════════════════════════════════════════

def _html_elements(path: Path) -> tuple:
    """用 html.parser 解析页面, 返回 ([(tag, attrs_dict)], [内联 script 文本])。注释里的文字不算元素。"""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.els = []
            self.scripts = []
            self._in_script = False

        def handle_starttag(self, tag, attrs):
            self.els.append((tag, {k: (v or "") for k, v in attrs}))
            self._in_script = tag == "script"

        def handle_startendtag(self, tag, attrs):
            self.els.append((tag, {k: (v or "") for k, v in attrs}))

        def handle_endtag(self, tag):
            if tag == "script":
                self._in_script = False

        def handle_data(self, data):
            if self._in_script:
                self.scripts.append(data)

    p = _P()
    p.feed(Path(path).read_text())
    p.close()
    return p.els, p.scripts


def _has_class(attrs: dict, cls: str) -> bool:
    return cls in (attrs.get("class") or "").split()


def _css_rules(path: Path) -> list:
    """极简 CSS 解析: 去注释后取最内层 `selector{decls}` 规则 (@media 等包裹层自动展开), 返回 [(selectors, decls)]。"""
    css = re.sub(r"/\*.*?\*/", "", Path(path).read_text(), flags=re.S)
    return [(sel.strip(), body.strip()) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def _css_class_rule(rules: list, cls: str, standalone: bool = False) -> bool:
    """是否存在 *带声明* 的规则选中类 cls。standalone=True 要求某个选择器恰好就是 `.cls` 本身。"""
    pat = re.compile(r"\." + re.escape(cls) + r"(?![\w-])")
    for sels, body in rules:
        if ":" not in body:
            continue
        for s in (x.strip() for x in sels.split(",")):
            if standalone and s == "." + cls:
                return True
            if not standalone and pat.search(s):
                return True
    return False


def _strip_js_comments(src: str) -> str:
    """去掉 JS 的 /* */ 块注释与行首/空白后的 // 行注释 (不碰字符串里的 http://), 只留代码。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", src)


def _run_subagent_scripted(rounds: list, agent_type: str = "critic"):
    """用脚本化的假 LLM 流真跑 lib.agent.subagent._run_subagent。

    rounds: 每轮一个 chunk 列表 (OpenAI 流式 chunk dict)。工具调用走假 execute_tool。
    返回 (result, log_messages, executed_tools)。
    """
    import asyncio
    import logging
    from lib.agent import subagent as sa
    from lib.config import log as _log

    msgs, executed = [], []
    it = iter(rounds)

    class _H(logging.Handler):
        def emit(self, rec):
            msgs.append(rec.getMessage())

    async def _fake_stream(messages, tools):
        for ch in next(it, [{"choices": [{"delta": {"content": "兜底结束"}}]}]):
            yield ch

    async def _fake_exec(name, args, sid):
        executed.append((name, args))
        return f"fake result of {name}", None

    h = _H()
    saved = (sa._vllm_stream, sa.execute_tool)
    _log.addHandler(h)
    sa._vllm_stream, sa.execute_tool = _fake_stream, _fake_exec
    try:
        result = asyncio.run(sa._run_subagent("脚本化任务", agent_type))
    finally:
        sa._vllm_stream, sa.execute_tool = saved
        _log.removeHandler(h)
    return result, msgs, executed


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
