"""execute_shell 超时连根杀进程组的回归用例 (真跑子进程, 用临时目录)。

背景 (2026-09-04): 旧 _exec_sync 用 subprocess.run(timeout=...), 超时只 Popen.kill()
直接子进程 (bash); start_new_session=True 又把孙进程放进独立进程组 —— 组没人杀,
孙进程 (paramiko sftp / python deploy.py 等) 成孤儿继续跑, 调用方也可能一直卡。
实测一条 python3 deploy.py 的 sftp.put 卡住, agent 主会话被冻 9 分钟。

核心用例 test_grandchild_reaped: 旧实现下孙进程存活 → FAIL; 新实现 killpg 收割 → PASS。
"""
import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import executor_v4 as ex  # noqa: E402


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class TestShellKillTree(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self._orig_retries = ex.MAX_RETRIES
        ex.MAX_RETRIES = 1          # 超时归 retryable, 压成 1 次让测试确定且快

    def tearDown(self):
        ex.MAX_RETRIES = self._orig_retries
        self._tmp.cleanup()

    def _run(self, cmd, timeout):
        return asyncio.run(ex.execute_shell_async(cmd, timeout=timeout, workspace=self.ws))

    # ── 正常命令不受重写影响 ──
    def test_normal_command(self):
        out = self._run("echo hello-kt", timeout=5)
        self.assertIn("hello-kt", out)

    def test_nonzero_exit_still_reported(self):
        out = self._run("echo boom >&2; exit 3", timeout=5)
        self.assertIn("boom", out)          # stderr 合并进 output

    def test_stderr_warning_on_success(self):
        # exit 0 但有 stderr → 保留 STDERR_WARNINGS 前缀 (重写后不能丢这条语义)
        out = self._run("echo ok; echo warn >&2", timeout=5)
        self.assertIn("ok", out)
        self.assertIn("STDERR_WARNINGS", out)

    # ── 超时语义 ──
    def test_timeout_message(self):
        out = self._run("sleep 30", timeout=2)
        self.assertIn("TIMEOUT", out)

    # ── 核心: 超时连根杀, 孙进程不留孤儿 ──
    def test_grandchild_reaped(self):
        pidfile = self.ws / "gc.pid"
        # bash 起一个后台 sleep (孙进程视角), 记录其 pid, 然后 wait 阻塞到超时
        cmd = f"sleep 60 & echo $! > {pidfile}; wait"
        out = self._run(cmd, timeout=2)
        self.assertIn("TIMEOUT", out)
        # 给收割一点时间
        time.sleep(1.0)
        self.assertTrue(pidfile.exists(), "后台进程 pid 应已写出")
        gc_pid = int(pidfile.read_text().strip())
        self.assertFalse(_pid_alive(gc_pid),
                         f"超时后台孙进程 {gc_pid} 应被 killpg 连根收割, 不能成孤儿续跑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
