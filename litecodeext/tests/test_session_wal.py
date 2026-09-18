"""会话 WAL 增量持久化 + 崩溃恢复的回归用例。

背景 (2026-09-04): 旧行为整轮 new_msgs 攒内存, 只在 agent_stream 的 finally 里
save_history 一次性落盘 —— 进程中途崩/容器重启就整轮丢。WAL 让每轮迭代增量落盘,
崩了下次 get_history 回放补回。核心用例 test_get_history_recovers_wal 模拟崩溃:
disk.json 停在旧状态、WAL 有未提交消息 → get_history 必须把它们补回并压实。
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import session as sess  # noqa: E402


class TestSessionWAL(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig_disk = sess.SESSIONS_DISK
        sess.SESSIONS_DISK = Path(self._tmp.name)
        sess._sessions.clear()

    def tearDown(self):
        sess.SESSIONS_DISK = self._orig_disk
        sess._sessions.clear()
        self._tmp.cleanup()

    # ── WAL 基本读写 ──
    def test_append_load_roundtrip(self):
        sess.wal_append("s1", [{"role": "assistant", "content": "步骤1"},
                               {"role": "tool", "content": "结果1"}])
        sess.wal_append("s1", [{"role": "assistant", "content": "步骤2"}])
        got = sess.wal_load("s1")
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]["content"], "步骤1")
        self.assertEqual(got[2]["content"], "步骤2")

    def test_append_empty_noop(self):
        sess.wal_append("s1", [])
        self.assertEqual(sess.wal_load("s1"), [])

    def test_clear(self):
        sess.wal_append("s1", [{"role": "assistant", "content": "x"}])
        sess.wal_clear("s1")
        self.assertEqual(sess.wal_load("s1"), [])

    def test_corrupt_tail_discarded(self):
        # 模拟崩溃写一半: 最后一行是残缺 JSON
        sess.wal_append("s1", [{"role": "assistant", "content": "完整"}])
        p = sess.SESSIONS_DISK / "s1.wal"
        with p.open("a", encoding="utf-8") as f:
            f.write('{"role": "assistant", "content": "残缺没写完')  # 无收尾
        got = sess.wal_load("s1")
        self.assertEqual(len(got), 1, "残缺尾行应被丢弃, 完整行保留")
        self.assertEqual(got[0]["content"], "完整")

    # ── 提交清 WAL ──
    def test_save_history_clears_wal(self):
        sess.wal_append("s2", [{"role": "assistant", "content": "本轮"}])
        sess.save_history("s2", [{"role": "user", "content": "问"}],
                          [{"role": "assistant", "content": "答"}])
        self.assertEqual(sess.wal_load("s2"), [], "save_history 提交后 WAL 应清空")

    # ── 核心: 崩溃恢复 ──
    def test_get_history_recovers_wal(self):
        # 1) 先有一轮正常提交, disk.json 里有基线
        sess.save_history("s3", [], [{"role": "user", "content": "Q1"},
                                     {"role": "assistant", "content": "A1"}])
        sess._sessions.clear()  # 模拟进程重启, 清内存

        # 2) 新一轮跑到一半 (WAL 有增量) 就崩了 —— 没走到 save_history
        sess.wal_append("s3", [{"role": "user", "content": "Q2"},
                               {"role": "assistant", "content": "A2-崩溃前的推理和调用"}])
        sess._sessions.clear()  # 再次模拟重启

        # 3) 下次 get_history 必须把崩溃轮补回来
        hist = sess.get_history("s3")
        contents = [m["content"] for m in hist]
        self.assertIn("A1", contents, "已提交的基线还在")
        self.assertIn("A2-崩溃前的推理和调用", contents, "崩溃轮的消息必须被 WAL 恢复, 不能丢")
        # 4) 恢复后 WAL 已清 + 已压实进 disk (幂等, 不会二次恢复)
        self.assertEqual(sess.wal_load("s3"), [], "恢复后 WAL 应清空")
        sess._sessions.clear()
        hist2 = sess.get_history("s3")
        c2 = [m["content"] for m in hist2]
        self.assertEqual(c2.count("A2-崩溃前的推理和调用"), 1, "不能重复恢复 (幂等)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
