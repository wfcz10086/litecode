#!/usr/bin/env python3
"""
test_memory_atomic.py — v1.9 P38-c Memory 原子写盘验证

覆盖：
- T1 _atomic_write 写入正确内容
- T2 _atomic_write 不留 .tmp 文件（成功路径）
- T3 _atomic_write 覆盖已存在文件
- T4 grep 验证关键 l1_file write 已切换为 _atomic_write
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litecodeext"))
os.environ["LITECODE_TEST_OFFLINE"] = "1"


class TestMemoryAtomic(unittest.TestCase):

    def setUp(self):
        for m in list(sys.modules.keys()):
            if m == "core" or m.startswith("core."):
                del sys.modules[m]
        from core import memory
        self.memory = memory

    def test_t1_atomic_write_correct_content(self):
        """T1: _atomic_write 写入内容完整"""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="atom_"))
        target = tmp / "test.txt"
        self.memory._atomic_write(target, "hello atomic\n含中文测试\n")
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), "hello atomic\n含中文测试\n")

    def test_t2_no_tmp_residue(self):
        """T2: 成功写入后不留 .tmp 文件"""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="atom_"))
        target = tmp / "test.txt"
        self.memory._atomic_write(target, "ok")
        # .tmp 应被 rename 掉
        tmp_file = target.with_suffix(target.suffix + ".tmp")
        self.assertFalse(tmp_file.exists(),
                         f".tmp 残留: {tmp_file}")

    def test_t3_overwrite_existing(self):
        """T3: 覆盖已存在文件"""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="atom_"))
        target = tmp / "test.txt"
        target.write_text("old content")
        self.memory._atomic_write(target, "new content")
        self.assertEqual(target.read_text(), "new content")

    def test_t4_l1_writes_atomic(self):
        """T4: 关键 l1_file 写入路径已切换为 _atomic_write（直接 或 P50 包装）"""
        # [#26] memory.py 拆包后, _atomic_write 定义在 iohelp.py, 调用在 manager.py
        iohelp_src  = (ROOT / "litecodeext" / "core" / "memory" / "iohelp.py").read_text()
        manager_src = (ROOT / "litecodeext" / "core" / "memory" / "manager.py").read_text()
        self.assertIn("def _atomic_write", iohelp_src)
        direct  = manager_src.count("_atomic_write(self.l1_file")
        wrapped = manager_src.count("_atomic_write_if_changed(self.l1_file")
        self.assertGreaterEqual(direct + wrapped, 3,
                                f"l1_file 写盘调用(直接+包装)应 ≥3, 实际 {direct + wrapped}")

    def test_t5_l1_init_uses_raw_write_acceptable(self):
        """T5: _init_l1 用 write_text 是可接受的（首次创建无并发风险）"""
        # 不强制要求所有 write_text 都改 atomic
        # _init_l1 在 __init__ 调用，单线程，可保留原写法
        # 这个测试只是文档化决策
        pass

    def test_t6_atomic_uses_os_replace(self):
        """T6: 实现使用 os.replace（POSIX 原子 rename）"""
        # [#26] 拆包后 _atomic_write 迁至 iohelp.py
        src = (ROOT / "litecodeext" / "core" / "memory" / "iohelp.py").read_text()
        import re
        m = re.search(r"def _atomic_write.*?(?=\ndef |\nclass |\Z)", src, re.S)
        self.assertIsNotNone(m, "找不到 _atomic_write 函数")
        body = m.group(0)
        self.assertIn("os.replace", body, f"_atomic_write 应用 os.replace: {body[:200]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
