"""core/executor_v4.py: 只有网络类命令碰到网络错误才自动重试, 其他非 0 退出原样返回。

2026-10-13 SWE-bench 日志: 复现脚本抛异常 / grep 输出里带 "502" 字样 都被当 retryable 重跑 3 次。
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
import executor_v4 as ex  # noqa: E402


class TestRetryPolicy(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(ex.classify_error("", 0), "ok")
        self.assertEqual(ex.classify_error("Traceback ...\nValueError: x", 1), "failed")
        self.assertEqual(ex.classify_error("curl: (28) Operation timed out after 502 ms timeout", 28), "retryable")
        self.assertEqual(ex.classify_error("Name or service not known", 1), "permanent")

    def test_network_cmd(self):
        for c in ("curl -s http://x", "pip install foo", "git clone http://x", "docker pull img"):
            self.assertTrue(ex.is_network_cmd(c), c)
        for c in ("python repro.py", "grep -n 502 file.py", "pytest -x", "git diff"):
            self.assertFalse(ex.is_network_cmd(c), c)

    def test_non_network_failure_runs_once(self):
        d = Path(tempfile.mkdtemp())
        cmd = "echo run >> count.txt; echo 'upstream 503 timeout'; exit 3"   # 输出带 retryable 字样
        out = asyncio.new_event_loop().run_until_complete(ex._run_cmd(cmd, 10, d))
        self.assertTrue(out.startswith("ERROR[EXIT=3]"), out)
        self.assertEqual((d / "count.txt").read_text().count("run"), 1)   # 没被重跑


if __name__ == "__main__":
    unittest.main()
