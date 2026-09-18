"""reasoning_effort 兼容映射的回归用例。

背景 (2026-09-03): 各模型对"思考强度"的控制方式不一。官方 vLLM 新版支持
reasoning_effort: low/medium/high, 但**本自建 qwen3.8 收下但忽略它**
(实测 none/low/high 四个值全 HTTP 200, 而思考量乱序: none=940字 low=1338 high=780 —
 完全不按 effort 分级)。所以真控制仍靠 chat_template_kwargs + max_tokens+=budget。

兼容做法: vllm 分支两个都发 —— budget 兜底当前上游, reasoning_effort 给认它的上游预留。
这些用例锁住: budget→effort 映射与前端 🧠 chip 档位对齐, 且不覆盖调用方显式传的值。
"""
import sys
import unittest
from pathlib import Path

_LIB = str(Path(__file__).resolve().parent.parent / "lib")
sys.path.insert(0, _LIB)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import thinking_adapter as ta  # noqa: E402


class _FakeLive(dict):
    """冒充 lib.config._LIVE, 让 _live_cfg 读到我们要的 budget/enable。"""


def _inject(budget: int, enabled: bool = True, backend="vllm", explicit_effort=None):
    """在受控的 _LIVE 下跑 inject_params, 返回 payload。"""
    ta._active = None                       # 清缓存, 强制现读
    orig = ta._get_loaded_live
    ta._get_loaded_live = lambda: _FakeLive(
        enable_thinking=enabled, thinking_budget=budget,
        backend_type=backend, deployment="vllm",
    )
    try:
        payload = {"model": "qwen3.8-flash-next-local",
                   "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8192}
        if explicit_effort is not None:
            payload["reasoning_effort"] = explicit_effort
        return ta.inject_params(payload)
    finally:
        ta._get_loaded_live = orig
        ta._active = None


class TestEffortCompat(unittest.TestCase):
    # ── budget → effort 映射, 对齐前端档位 (低2048/中4096/高16384) ──
    def test_low_budget_maps_low(self):
        p = _inject(2048)
        self.assertEqual(p["reasoning_effort"], "low")

    def test_mid_budget_maps_medium(self):
        p = _inject(4096)
        self.assertEqual(p["reasoning_effort"], "medium")

    def test_high_budget_maps_high(self):
        p = _inject(16384)
        self.assertEqual(p["reasoning_effort"], "high")

    def test_boundary_8000_is_medium(self):
        self.assertEqual(_inject(8000)["reasoning_effort"], "medium")

    def test_boundary_8001_is_high(self):
        self.assertEqual(_inject(8001)["reasoning_effort"], "high")

    # ── 兼容: budget 机制必须仍在 (真控制靠它) ──
    def test_budget_mechanism_still_present(self):
        p = _inject(4096)
        self.assertIn("chat_template_kwargs", p)
        self.assertTrue(p["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(p["max_tokens"], 8192 + 4096, "max_tokens 必须仍 += budget")

    # ── 关闭思考时不发 effort ──
    def test_disabled_no_effort(self):
        p = _inject(4096, enabled=False)
        self.assertNotIn("reasoning_effort", p)
        self.assertFalse(p["chat_template_kwargs"]["enable_thinking"])

    # ── 调用方显式传的 effort 不被覆盖 ──
    def test_explicit_effort_preserved(self):
        p = _inject(2048, explicit_effort="high")
        self.assertEqual(p["reasoning_effort"], "high",
                         "显式传的 effort 优先, 不被 budget 映射覆盖")


if __name__ == "__main__":
    unittest.main(verbosity=2)
