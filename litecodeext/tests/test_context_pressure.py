"""lib/context_pressure.py: 上下文没压力时不缩窗口 (2026-10-13 SWE-bench: 128K 窗口第 10 轮就砍读过的代码)。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import context_pressure as cp  # noqa: E402


def est(x):
    return len(str(x or "")) // 4


def usage(reports=0, real=0, at=0, fixed=0):
    return SimpleNamespace(usage_reports=reports, last_real_prompt_tokens=real, last_est_at_anchor=at,
                           fixed_overhead=fixed)


MSGS = [{"role": "tool", "content": "x" * 4000} for _ in range(30)]   # 约 3 万 token


class TestContextPressure(unittest.TestCase):
    def test_no_anchor_uses_history_plus_fixed(self):
        self.assertEqual(cp.prompt_tokens(MSGS, usage(fixed=13000), est), 30000 + 13000)

    def test_anchor(self):
        self.assertEqual(cp.prompt_tokens(MSGS, usage(reports=1, real=40000, at=25000), est), 40000 + 5000)

    def test_pressure_threshold(self):
        budget = int(131072 * 0.6)                       # 78643
        self.assertFalse(cp.under_pressure(MSGS, usage(fixed=13000), est, budget, 0.6))   # 43000 < 47186
        self.assertTrue(cp.under_pressure(MSGS, usage(fixed=13000), est, budget, 0.5))    # 43000 >= 39321


if __name__ == "__main__":
    unittest.main()
