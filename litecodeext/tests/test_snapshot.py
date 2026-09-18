"""框架级工作区快照的回归用例 (真跑 git, 用临时目录, 不碰运行中的服务)。"""
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.snapshot import (  # noqa: E402
    infer_project_root, snapshot, list_snapshots, restore,
)


def _has_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


@unittest.skipUnless(_has_git(), "宿主没有 git")
class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "ws"
        self.proj = Path(self._tmp.name) / "proj"
        self.ws.mkdir(); self.proj.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, content):
        p = self.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return str(p)

    # ── 项目根推断 ──
    def test_infer_common_parent(self):
        a = self._write("internal/a.go", "1")
        b = self._write("internal/b.go", "2")
        root = infer_project_root([a, b])
        self.assertEqual(root, str(self.proj / "internal"))

    def test_infer_rejects_system_dirs(self):
        # 共同父是 /tmp 这种系统目录时不当项目
        self.assertIsNone(infer_project_root(["/tmp/x.txt", "/tmp/y.txt"]))

    def test_infer_empty_returns_fallback(self):
        self.assertEqual(infer_project_root([], fallback="/some/root"), "/some/root")

    # ── 快照 + 恢复 (核心回滚场景) ──
    def test_snapshot_then_restore(self):
        self._write("main.go", "version 1")
        s1 = snapshot(self.ws, str(self.proj), "task-1")
        self.assertIsNotNone(s1, "第一次快照应成功")
        assert s1 is not None

        # agent 把文件改坏
        (self.proj / "main.go").write_text("version 2 — 改崩了")
        (self.proj / "junk.txt").write_text("快照后新增的垃圾")
        snapshot(self.ws, str(self.proj), "task-2")

        # 回滚到 s1
        ok, msg = restore(self.ws, str(self.proj), s1)
        self.assertTrue(ok, msg)
        self.assertEqual((self.proj / "main.go").read_text(), "version 1",
                         "回滚后文件内容应恢复")
        self.assertFalse((self.proj / "junk.txt").exists(),
                         "回滚应清掉快照后新增的未跟踪文件")

    def test_restore_self_saves_first(self):
        # 回滚前会自动快照当前状态, 使"回滚回滚"成为可能
        self._write("f.txt", "A")
        sA = snapshot(self.ws, str(self.proj), "A")
        assert sA is not None
        (self.proj / "f.txt").write_text("B")
        snapshot(self.ws, str(self.proj), "B")
        restore(self.ws, str(self.proj), sA)          # 回到 A
        snaps = list_snapshots(self.ws, str(self.proj))
        labels = " ".join(s["label"] for s in snaps)
        self.assertIn("pre-restore", labels, "回滚前应留有自保存快照")

    # ── 列表 ──
    def test_list_orders_newest_first(self):
        self._write("f.txt", "1")
        snapshot(self.ws, str(self.proj), "first")
        self._write("f.txt", "2")
        snapshot(self.ws, str(self.proj), "second")
        snaps = list_snapshots(self.ws, str(self.proj))
        self.assertGreaterEqual(len(snaps), 2)
        self.assertIn("second", snaps[0]["label"])

    # ── 鲁棒性: 绝不抛 ──
    def test_snapshot_bad_root_returns_none(self):
        self.assertIsNone(snapshot(self.ws, "/nonexistent/xyz", "x"))

    def test_restore_no_snapshots(self):
        ok, _ = restore(self.ws, str(self.proj), "deadbeef")
        self.assertFalse(ok)

    def test_gitignore_respected(self):
        self._write(".gitignore", "*.log\n")
        self._write("keep.go", "code")
        self._write("noise.log", "should be ignored")
        snapshot(self.ws, str(self.proj), "t")
        # noise.log 不该进快照: 改它再回滚, 内容不受影响 (未被跟踪)
        gitdir = self.ws / ".snapshots"
        # 用 git ls-files 确认
        import hashlib
        h = hashlib.md5(str(self.proj).encode()).hexdigest()[:12]
        r = subprocess.run(["git", f"--git-dir={gitdir}/{h}.git",
                            f"--work-tree={self.proj}", "ls-files"],
                           cwd=str(self.proj), capture_output=True, text=True)
        self.assertIn("keep.go", r.stdout)
        self.assertNotIn("noise.log", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
