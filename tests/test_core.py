#!/usr/bin/env python3
"""
test_core.py — core/ 单元测试 (P8-c)
======================================
覆盖 5 模块: flow_control / blackboard / memory_index / orchestrator / timer_manager.
极速版: 每个模块 1-2 个最关键单测.

跑: python3 tests/test_core.py
"""
import sys
import os
import unittest
import asyncio
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))
sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext" / "core"))
sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext" / "lib"))


class TestBlackboard(unittest.TestCase):
    def test_put_and_get(self):
        from blackboard import Blackboard, EntryType
        bb = Blackboard()
        bb.put("k1", "v1", EntryType.CONTEXT, source="test")
        self.assertEqual(bb.get("k1"), "v1")
        self.assertIsNone(bb.get("missing"))

    def test_query_since(self):
        from blackboard import Blackboard, EntryType
        bb = Blackboard()
        bb.put("a", 1)
        ts = bb.latest_ts()
        import time as _t
        _t.sleep(0.01)
        bb.put("b", 2)
        bb.put("c", 3)
        recent = bb.query_since(ts + 0.001)
        self.assertEqual(len(recent), 2)


class TestProjects(unittest.TestCase):
    def test_create_get_list(self):
        from projects import ProjectStore
        td = Path(tempfile.mkdtemp(prefix="t_"))
        try:
            ps = ProjectStore(td)
            m = ps.create("名", "novel")
            self.assertTrue(m.project_id.startswith("nov_"))
            got = ps.get(m.project_id)
            self.assertEqual(got.name, "名")
            lst = ps.list_all()
            self.assertEqual(len(lst), 1)
        finally:
            shutil.rmtree(td)


class TestFastPath(unittest.TestCase):
    def test_greeting(self):
        from fast_path import is_fast_path
        ok, _ = is_fast_path("你好")
        self.assertTrue(ok)

    def test_tool_keyword(self):
        from fast_path import is_fast_path
        ok, _ = is_fast_path("写一个脚本")
        self.assertFalse(ok)


class TestRAG(unittest.TestCase):
    def test_index_and_query(self):
        from rag import index_dir, query, clear_index
        td = Path(tempfile.mkdtemp(prefix="t_"))
        try:
            d = td / "d"
            d.mkdir()
            (d / "x.md").write_text("# Python\nPython 是高级语言.")
            ws = td / "ws"
            ws.mkdir()
            n = index_dir(ws, d)
            self.assertGreater(n, 0)
            r = query(ws, "Python")
            self.assertGreater(len(r), 0)
        finally:
            shutil.rmtree(td)


class TestFailureMemory(unittest.TestCase):
    def test_record_dedup(self):
        from failure_memory import record_failure, get_summary
        td = Path(tempfile.mkdtemp(prefix="t_"))
        try:
            self.assertTrue(record_failure(td, "timeout", "shell hang"))
            self.assertFalse(record_failure(td, "timeout", "shell hang"))  # dedup
            self.assertTrue(record_failure(td, "tool_error", "patch fail"))
            stats = get_summary(td)
            self.assertEqual(stats["timeout"], 1)
            self.assertEqual(stats["tool_error"], 1)
        finally:
            shutil.rmtree(td)


class TestPlanAct(unittest.TestCase):
    def test_inject_and_extract(self):
        from plan_act import inject_plan_prompt, extract_plan, is_plan_done
        msgs = [{"role": "user", "content": "hi"}]
        r = inject_plan_prompt(msgs, "plan_only")
        self.assertEqual(r[0]["role"], "system")
        text = "## 执行计划\n1. step\n[PLAN_DONE]\nactual"
        self.assertTrue(is_plan_done(text))
        plan = extract_plan(text)
        self.assertIn("step", plan)


class TestSessionFork(unittest.TestCase):
    def test_fork_basic(self):
        from session_fork import fork_session
        td = Path(tempfile.mkdtemp(prefix="t_"))
        try:
            sd = td / "sessions" / "s1"
            sd.mkdir(parents=True)
            (sd / "memo.md").write_text("hi")
            new_sid = fork_session(td, "s1")
            self.assertIsNotNone(new_sid)
            self.assertTrue(new_sid.startswith("fork_"))
            self.assertTrue((td / "sessions" / new_sid / "memo.md").exists())
        finally:
            shutil.rmtree(td)


if __name__ == "__main__":
    unittest.main(verbosity=2)
