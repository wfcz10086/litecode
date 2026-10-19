"""框架守卫别把正常推进当死循环 / 违规 (2026-10-16 "天才交易员" 视频任务日志)。

- itrace 频率检测: 连建 6 个 task_create、给 5 个文件 update_map 被判 LOGIC_LOOP,
  3 连后 SYSTEM OVERRIDE 把真实结果清掉
- batch guard: 刚被 SYSTEM-TEST 要求写测试, 写了测试文件又被骂"批量产出"; 中间跑过验证也不清零
- loop guard: apply_blocks 改完 HTML 再跑同一条截图命令, 第 3 次被拦
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [_ROOT, os.path.join(_ROOT, "core")]
from core.itrace import Tracer  # noqa: E402
from core import tool_dispatch as td  # noqa: E402
from plugins.tools.guard import ToolLoopGuard  # noqa: E402


def tracer():
    d = tempfile.mkdtemp()
    return Tracer({"enabled": True, "save_path": f"{d}/it.log"}, workspace=__import__("pathlib").Path(d))


class TestItraceFrequency(unittest.TestCase):
    def loops(self, calls):
        t = tracer()
        flags = []
        for name, args in calls:
            t.tool_call(name, args, result=f"ok {args}", elapsed=0.01)
            flags.append(t._last_loop)
        return flags

    def test_distinct_task_creates_not_loop(self):
        calls = [("task_create", {"title": f"step{i}: 做第 {i} 件事"}) for i in range(7)]
        self.assertFalse(any(self.loops(calls)))

    def test_distinct_update_map_not_loop(self):
        calls = [("update_map", {"filepath": f"f{i}.py"}) for i in range(6)]
        self.assertFalse(any(self.loops(calls)))

    def test_same_args_still_loop(self):
        calls = [("vision_ocr", {"path": "/x/a.png"}), ("execute_shell", {"command": "ls"}),
                 ("vision_ocr", {"path": "/x/a.png"}), ("execute_shell", {"command": "pwd"}),
                 ("vision_ocr", {"path": "/x/a.png"}), ("vision_ocr", {"path": "/x/a.png"})]
        self.assertTrue(self.loops(calls)[-1])


class TestBatchWriteGuard(unittest.TestCase):
    def setUp(self):
        td._write_streak.clear()

    def test_test_files_exempt(self):
        sid = "s-test"
        self.assertEqual(td._check_batch_write_guard(sid, "/p/engine.js", True), "")
        self.assertEqual(td._check_batch_write_guard(sid, "/p/video.html", True), "")
        self.assertEqual(td._check_batch_write_guard(sid, "/p/engine.test.js", True), "")
        self.assertEqual(td._check_batch_write_guard(sid, "/p/tests/test_render.py", True), "")

    def test_shell_run_resets_streak(self):
        sid = "s-reset"
        td._check_batch_write_guard(sid, "/p/a.py", True)
        td._check_batch_write_guard(sid, "/p/b.py", True)
        td.note_shell_run(sid)
        self.assertEqual(td._check_batch_write_guard(sid, "/p/c.py", True), "")

    def test_three_sources_without_verify_still_warned(self):
        sid = "s-batch"
        for f in ("a.py", "b.py"):
            td._check_batch_write_guard(sid, f"/p/{f}", True)
        self.assertIn("SYSTEM-BATCH-GUARD", td._check_batch_write_guard(sid, "/p/c.py", True))


class TestLoopGuardApplyBlocks(unittest.TestCase):
    def test_apply_blocks_resets_history(self):
        g = ToolLoopGuard()
        shot = ("execute_shell", {"command": "cd /w && python3 shot.py cover.html cover.png 1080 1440"})
        for i in range(4):
            self.assertFalse(g.check(*shot)[0])
            self.assertFalse(g.check(*shot)[0])
            g.check("apply_blocks", {"blocks": f"cover.html\n<<<<<<< SEARCH\ntop:{i}px"})   # 每次改的不一样
        self.assertEqual(g.block_count, 0)
        same = ("apply_blocks", {"blocks": "cover.html\n<<<<<<< SEARCH\ntop:1px"})
        g2 = ToolLoopGuard()
        self.assertFalse(g2.check(*same)[0])
        self.assertFalse(g2.check(*same)[0])
        self.assertTrue(g2.check(*same)[0])                      # 同一块连打 3 次照拦


class TestLoopGuardFullArguments(unittest.TestCase):
    def test_long_ssh_log_queries_do_not_collide(self):
        # VS Code 日志的公共目录很长，文件名与 Tail 差异在旧签名的 200 字之后。
        directory = (r"C:\Users\litecode\AppData\Roaming\Code\logs\20261019T161500"
                     "\\window1\\exthost\\ms-vscode-remote.remote-containers\\"
                     "开发容器诊断\\" + "long-log-directory-" * 8)
        guard = ToolLoopGuard()
        signatures = []
        for name, tail in (("devContainers.log", 30), ("extension.log", 30),
                           ("extension.log", 80), ("devContainers.log", 80)):
            args = {"alias": "win-devtest", "shell": "powershell", "timeout": 40,
                    "cmd": f"Get-Content -LiteralPath '{directory}\\{name}' -Tail {tail}"}
            blocked, warning, signature = guard.check("ssh_exec", args)
            self.assertFalse(blocked)
            self.assertIsNone(warning)
            signatures.append(signature)
        self.assertEqual(len(set(signatures)), 4)
        self.assertEqual(guard.block_count, 0)

    def test_identical_long_ssh_query_is_still_blocked(self):
        args = {"alias": "win-devtest", "shell": "powershell", "timeout": 40,
                "cmd": "Get-Content -LiteralPath 'C:\\logs\\" + "same-directory\\" * 30
                       + "devContainers.log' -Tail 80"}
        guard = ToolLoopGuard()
        self.assertFalse(guard.check("ssh_exec", args)[0])
        # JSON object key order is irrelevant to tool semantics and signatures.
        reordered = dict(reversed(list(args.items())))
        self.assertFalse(guard.check("ssh_exec", reordered)[0])
        blocked, warning, signature = guard.check("ssh_exec", args)
        self.assertTrue(blocked)
        self.assertIn("SYSTEM-LOOP-GUARD", warning)
        self.assertIn(signature, guard.break_sent)
        self.assertTrue(guard.check("ssh_exec", reordered)[0])
        self.assertEqual(guard.block_count, 2)


if __name__ == "__main__":
    unittest.main()
