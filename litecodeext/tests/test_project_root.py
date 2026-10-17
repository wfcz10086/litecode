"""lib/project_root.py: 单文件脚本不能被当成项目 (否则提醒去建 xxx.py/PROJECT_MAP.md)。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.project_root import infer_project_root, is_oneoff  # noqa: E402

WS = "/tmp/litecode_workspace"


class TestProjectRoot(unittest.TestCase):
    def test_subdir_is_project(self):
        self.assertEqual(infer_project_root(WS, f"{WS}/livermore/render.py"), f"{WS}/livermore")
        self.assertEqual(infer_project_root(WS + "/", f"{WS}/a/b/c.py"), f"{WS}/a")

    def test_script_in_workspace_root_is_not_a_project(self):
        self.assertEqual(infer_project_root(WS, f"{WS}/win11_shot2.py"), "")

    def test_outside_workspace(self):
        self.assertEqual(infer_project_root(WS, "/opt/other/x.py"), "")
        self.assertEqual(infer_project_root(WS, f"{WS}x/y.py"), "")      # 前缀相同但不是子目录

    def test_relative_path_resolved_like_write_file(self):
        self.assertEqual(infer_project_root(WS, "livermore/render.py"), f"{WS}/livermore")
        self.assertEqual(infer_project_root(WS, "./a/b/c.py"), f"{WS}/a")
        self.assertEqual(infer_project_root(WS, "render.py"), "")                # 工作区根目录脚本仍不算项目
        self.assertEqual(infer_project_root(WS, "../etc/x/y.py"), "")           # 拼出工作区之外

    def test_oneoff_names(self):
        for n in ("probe_ssh.py", "tmp_fix.py", "scratch.py", "_x.py", "Probe2.py"):
            self.assertTrue(is_oneoff(n), n)
        for n in ("win11_shot.py", "render.py", "server.py"):
            self.assertFalse(is_oneoff(n), n)


if __name__ == "__main__":
    unittest.main()
