"""无 SearXNG 时的浏览器引擎调度: 中文查询 bing/sogou 优先, 失败引擎冷却不再排, 首选引擎快就不惊动其它 (2026-10-22 桌面 45s→8s)。"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]
import litecode_server  # noqa: E402,F401
from plugins.tools import web_search as W  # noqa: E402

GOOD = "## 浏览器搜索结果 (x, 5 条)\n\n[1] **上证指数行情走势** URL: https://quote.eastmoney.com/ 东方财富"


def _run(monkeypatch, behave, query="今天A股行情"):
    calls = []

    def fallback(q, eng):
        calls.append(eng)
        d, res = behave.get(eng, (0, None))
        time.sleep(d)
        return res
    bs = SimpleNamespace(browser_search_fallback=fallback)   # 没有 searxng_search → 直接走浏览器
    monkeypatch.setattr(W, "_td", SimpleNamespace(_bs=bs, _ds=None, HAS_BROWSER_SEARCH=True, HAS_SEARCH=False,
                                                    _SEARCH_CFG={"default_mode": "domestic"}))
    monkeypatch.setattr("lib.tool_trace.report", lambda *a, **k: None)
    t = time.time()
    res, _ = asyncio.run(W.tool_web_search({"query": query}, None, {}))
    return calls, res, time.time() - t


def test_cjk_prefers_bing_and_skips_others_when_fast(monkeypatch):
    monkeypatch.setattr(W, "_ENGINE_COOLDOWN", {})
    calls, res, dt = _run(monkeypatch, {"bing": (0.1, GOOD)})
    assert calls == ["bing"] and res == GOOD and dt < 3


def test_failed_engine_cooled_down(monkeypatch):
    monkeypatch.setattr(W, "_ENGINE_COOLDOWN", {})
    calls, res, _ = _run(monkeypatch, {"bing": (0, None), "sogou": (0.1, GOOD)})
    assert calls[:2] == ["bing", "sogou"] and res == GOOD
    assert W._ENGINE_COOLDOWN.get("bing", 0) > time.time()
    calls, res, _ = _run(monkeypatch, {"sogou": (0.1, GOOD)})
    assert calls == ["sogou"]
