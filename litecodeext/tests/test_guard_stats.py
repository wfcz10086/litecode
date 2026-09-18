"""守卫注入统计的回归用例。

背景 (2026-08-31 实测): `[SYSTEM-TEST]` 守卫用 Python/JS 的测试命名约定检测 Go 项目,
从 iter 3 误报到 iter 79 —— 每轮往 agent 上下文注入一条内容错误的警告。
**误报了 79 轮无人发现, 因为没有任何地方统计守卫注入了多少次。**

这些用例锁住的正是"让空转守卫现形"这个能力本身。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.guard_stats import GuardStats, load_totals  # noqa: E402


class TestGuardStats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # ── 空回合不产生噪音 ──
    def test_no_injection_no_summary_no_file(self):
        gs = GuardStats(workspace=self.ws, session_id="s1")
        self.assertEqual(gs.summary(), "", "没有注入时不该打印任何东西")
        gs.flush()
        self.assertFalse((self.ws / "guard_stats.json").exists(),
                         "没有注入时不该生成文件")

    # ── 计数与摘要 ──
    def test_counts_and_summary(self):
        gs = GuardStats(workspace=self.ws, session_id="s1")
        for _ in range(79):
            gs.hit("SYSTEM-TEST")      # 复刻那次 79 轮误报
        gs.hit("SYSTEM-PLAN-GATE")
        self.assertEqual(gs.counts["SYSTEM-TEST"], 79)
        s = gs.summary()
        self.assertIn("SYSTEM-TEST=79", s)
        self.assertIn("SYSTEM-PLAN-GATE=1", s)

    # ── 落盘累计 ──
    def test_flush_accumulates_across_turns(self):
        for n in (3, 5):
            gs = GuardStats(workspace=self.ws, session_id="s1")
            for _ in range(n):
                gs.hit("SYSTEM-TEST")
            gs.flush()
        totals = load_totals(self.ws)
        self.assertEqual(totals["SYSTEM-TEST"]["injections"], 8, "两回合应累加为 8")
        self.assertEqual(totals["SYSTEM-TEST"]["turns"], 2, "应记录跨越 2 个回合")
        self.assertTrue(totals["SYSTEM-TEST"]["last_seen"])

    # ── 多守卫独立计数 ──
    def test_multiple_guards_independent(self):
        gs = GuardStats(workspace=self.ws, session_id="s1")
        gs.hit("SYSTEM-TEST", 4)
        gs.hit("SYSTEM-READONLY", 2)
        gs.flush()
        totals = load_totals(self.ws)
        self.assertEqual(totals["SYSTEM-TEST"]["injections"], 4)
        self.assertEqual(totals["SYSTEM-READONLY"]["injections"], 2)

    # ── 埋点绝不能弄崩主流程 ──
    def test_flush_never_raises_on_bad_workspace(self):
        gs = GuardStats(workspace=Path("/nonexistent/deeply/nested"), session_id="s1")
        gs.hit("SYSTEM-TEST")
        gs.flush()          # 只应 log.warning, 不抛

    def test_flush_survives_corrupt_existing_file(self):
        (self.ws / "guard_stats.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
        gs = GuardStats(workspace=self.ws, session_id="s1")
        gs.hit("SYSTEM-TEST")
        gs.flush()          # 坏文件应被覆盖重建, 不抛
        self.assertEqual(load_totals(self.ws)["SYSTEM-TEST"]["injections"], 1)

    def test_load_totals_missing_file_returns_empty(self):
        self.assertEqual(load_totals(self.ws), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
