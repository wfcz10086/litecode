"""lib/tool_filter.py: 单次请求禁用工具 (评测断网)。

2026-10-13 SWE-bench flashnext-v1: 50 题里 21 题用 web_fetch 直接拉了官方 PR diff, 分数作废。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import tool_filter as tf  # noqa: E402
from lib.tools.defs import TOOL_DEFS  # noqa: E402


class TestToolFilter(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(tf.parse(None), frozenset())
        self.assertEqual(tf.parse("spawn_agent, git_log"), {"spawn_agent", "git_log"})
        d = tf.parse(["web", "spawn_agent"])
        self.assertIn("web_fetch", d)
        self.assertIn("browser_read", d)
        self.assertIn("spawn_agent", d)

    def test_visible(self):
        self.assertIs(tf.visible(TOOL_DEFS, frozenset()), TOOL_DEFS)     # 不禁用: 同一对象, 不破缓存
        names = {t["function"]["name"] for t in tf.visible(TOOL_DEFS, tf.parse("web"))}
        self.assertNotIn("web_fetch", names)
        self.assertNotIn("web_search", names)
        self.assertIn("read_file", names)
        self.assertIn("execute_shell", names)

    def test_group_names_exist(self):
        all_names = {t["function"]["name"] for t in TOOL_DEFS}
        self.assertTrue(tf.GROUPS["web"] <= all_names, tf.GROUPS["web"] - all_names)


if __name__ == "__main__":
    unittest.main()
