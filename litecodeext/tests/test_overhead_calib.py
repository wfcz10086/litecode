"""lib/overhead_calib.py: 固定开销用上游实测值, 没学到时不让估算吃光历史预算。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import overhead_calib as oc  # noqa: E402


class TestOverheadCalib(unittest.TestCase):
    def setUp(self):
        oc.reset()

    def test_cap_before_learning(self):
        # 2026-10-12 实测: 估 43609, 预算 38400 → 以前历史额度被压到 4096 下限
        self.assertEqual(oc.adjust("m", 43609, 38400), 19200)
        self.assertEqual(oc.adjust("m", 9000, 38400), 9000)

    def test_learned_value_wins(self):
        oc.learn("m", 36677, 23439)
        self.assertEqual(oc.adjust("m", 43609, 38400), 13238)
        self.assertEqual(oc.adjust("other", 43609, 38400), 19200)   # 按模型分开

    def test_ignore_nonsense(self):
        oc.learn("m", 1000, 5000)       # 负差值: 上游漏报
        oc.learn("m", "x", 1)
        self.assertEqual(oc.adjust("m", 43609, 38400), 19200)


if __name__ == "__main__":
    unittest.main()
