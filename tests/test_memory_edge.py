#!/usr/bin/env python3
"""
test_memory_edge.py — v1.9 P37-c Memory 边界回归测试

覆盖:
- T1 _validate_compressed 各 reject 路径（短/激进/太多行/无段/丢保护段）
- T2 _validate_compressed 通过路径
- T3 rule_based_memory_update 用户名提取
- T4 OFFLINE 模式 smart_auto_memory 跳过 LLM
- T5 _validate_compressed 不丢"Errors & Corrections" 保护段

不依赖真实 LLM/网络。
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litecodeext"))

# 强制 OFFLINE
os.environ["LITECODE_TEST_OFFLINE"] = "1"


class TestMemoryEdge(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 清缓存
        for m in list(sys.modules.keys()):
            if m == "core" or m.startswith("core.") or m == "memory":
                del sys.modules[m]
        from core import memory
        cls.mem = memory

    def test_t1_validate_too_short(self):
        """T1: 压缩结果 < 200 字 → reject"""
        new_text = "## summary\n太短了."
        old_text = "old " * 500  # 2000 字
        ok, reason = self.mem._validate_compressed(new_text, old_text)
        self.assertFalse(ok)
        self.assertIn("too short", reason)

    def test_t2_validate_too_aggressive(self):
        """T2: old > 1000 字 且 new < 5% old → reject"""
        new_text = "## summary\n" + "x" * 250  # 260 字（>200, 但 < 5% of 6000）
        old_text = "old " * 1500  # 6000 字
        ok, reason = self.mem._validate_compressed(new_text, old_text)
        self.assertFalse(ok)
        self.assertIn("too aggressively", reason)

    def test_t3_validate_no_sections(self):
        """T3: 压缩结果无 `##` markdown 段 → reject"""
        new_text = "free flowing text without sections " * 30  # 长但无段
        old_text = "old " * 100
        ok, reason = self.mem._validate_compressed(new_text, old_text)
        self.assertFalse(ok)
        self.assertIn("no ## sections", reason)

    def test_t4_validate_lost_protected_section(self):
        """T4: old 含 'Errors & Corrections' 但 new 丢失 → reject"""
        old_text = ("## summary\nx\n## Errors & Corrections\n"
                    + "important error log\n" * 30)
        new_text = "## summary\n" + "ok " * 100  # 长度足够，无 Errors & Corrections
        ok, reason = self.mem._validate_compressed(new_text, old_text)
        self.assertFalse(ok)
        self.assertIn("Errors & Corrections", reason)

    def test_t5_validate_pass(self):
        """T5: 合法压缩通过 — 含段、长度合理、保护段保留"""
        old_text = ("## summary\n" + "x" * 1000 + "\n"
                    "## Errors & Corrections\nerr1\n")
        new_text = ("## summary\n" + "compact " * 30 + "\n"
                    "## Errors & Corrections\nerr1 retained\n")
        ok, reason = self.mem._validate_compressed(new_text, old_text)
        self.assertTrue(ok, f"应通过但 reject: {reason}")

    def test_t6_rule_based_extract_user_name(self):
        """T6: rule_based 从对话提取用户名（基础规则提取）"""
        from core import memory

        class FakeMgr:
            def __init__(self):
                self.session_id = "edge-test"
                self._store = {}
            def save(self, section, content):
                self._store.setdefault(section, []).append(content)
            def load(self, section=None):
                if section:
                    return "\n".join(self._store.get(section, []))
                return str(self._store)

        mgr = FakeMgr()
        msgs = [
            {"role": "user", "content": "我叫张三，Python 后端"},
            {"role": "assistant", "content": "好的张三，请说"},
        ]
        result = memory.rule_based_memory_update(mgr, msgs)
        # 至少不抛异常 + 返回 bool
        self.assertIsInstance(result, bool)

    def test_t7_smart_auto_memory_offline_skip(self):
        """T7: OFFLINE env 下 smart_auto_memory 跳过 LLM 调用"""
        from core import memory

        class FakeMgr:
            def __init__(self):
                self.session_id = "off-test"
                self._store = {}
            def save(self, section, content):
                self._store.setdefault(section, []).append(content)
            def load(self, section=None):
                return str(self._store)

        mgr = FakeMgr()
        msgs = [
            {"role": "user", "content": "我叫张三"},
            {"role": "assistant", "content": "好的"},
        ]
        # 用不可达 URL — OFFLINE 应跳过 LLM 不挂起
        import time
        t0 = time.time()
        result = memory.smart_auto_memory(
            vllm_url="http://192.0.2.1:1",  # RFC 5737 不可达
            model_id="fake", api_key="fake",
            messages=msgs, manager=mgr,
        )
        elapsed = time.time() - t0
        # OFFLINE 应在毫秒级返回，绝不会触发 TCP SYN 超时
        self.assertLess(elapsed, 5.0,
                        f"OFFLINE 模式应快速返回，但耗时 {elapsed:.1f}s")
        self.assertIsInstance(result, bool)

    def test_t8_validate_too_many_lines(self):
        """T8: 压缩后行数 > L1_MAX_LINES * 3 → reject"""
        # L1_MAX_LINES = 200, 阈值 600
        # 构造 700 行以确保超出
        many_lines = "\n".join([f"## s{i}\nline\nmore" for i in range(250)])
        # 250 sections × 3 = 750 行
        new_text = many_lines
        old_text = "old " * 500
        ok, reason = self.mem._validate_compressed(new_text, old_text)
        self.assertFalse(ok, f"应 reject (>600 行) 但 PASS: {reason}")
        self.assertIn("too many lines", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
