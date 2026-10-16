"""版本号 + 绰号只有 lib/version.py 一个来源; README 的最新版本段必须跟它一致。"""
import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
from lib import version  # noqa: E402


class TestVersion(unittest.TestCase):
    def test_format(self):
        self.assertRegex(version.VERSION, r"^v\d+(\.\d+){2,3}$")
        self.assertTrue(version.CODENAME and version.CODENAME_EN)
        self.assertEqual(version.info()["codename"], version.CODENAME)

    def test_readmes_lead_with_current_version(self):
        for name, mark in (("README.md", version.CODENAME), ("README.en.md", version.CODENAME_EN)):
            p = os.path.join(ROOT, "..", name)
            if not os.path.exists(p):
                continue
            first = re.search(r"^### (v\S+) (.+)$", open(p, encoding="utf-8").read(), re.M)
            self.assertIsNotNone(first, name)
            self.assertEqual(first.group(1), version.VERSION, name)
            self.assertIn(mark, first.group(2), name)


if __name__ == "__main__":
    unittest.main()
