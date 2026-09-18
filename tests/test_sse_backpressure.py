#!/usr/bin/env python3
"""
test_sse_backpressure.py — v1.9 P38-b SSE 背压保护

验证：
- T1 client disconnect 检测路径存在（grep code）
- T2 chunk hard limit 路径存在
- T3 sse.jsonl 埋点配置正确
- T4 telemetry sse_stream_done 事件可写入
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litecodeext"))


class TestSSEBackpressure(unittest.TestCase):

    def test_t1_client_disconnect_check(self):
        """T1: web_ui.py 含 await request.is_disconnected() 检测"""
        src = (ROOT / "litecodeext" / "web_ui.py").read_text()
        self.assertIn("is_disconnected()", src,
                      "缺少客户端断开检测")
        # 检测后应 break 出循环
        # 验证有 _disconnected 标记
        self.assertIn("_disconnected = True", src)

    def test_t2_hard_limit_check(self):
        """T2: web_ui.py 含 SSE_HARD_LIMIT 保护"""
        src = (ROOT / "litecodeext" / "web_ui.py").read_text()
        self.assertIn("SSE_HARD_LIMIT", src)
        self.assertIn("SSE_HIGH_VOLUME_WARN", src)
        # 应有 hard_limit aborted 路径
        self.assertIn("hard_limit_", src)

    def test_t3_sse_telemetry_config(self):
        """T3: SSE 流写 sse.jsonl 埋点"""
        src = (ROOT / "litecodeext" / "web_ui.py").read_text()
        self.assertIn('jsonl="sse.jsonl"', src)
        # 三类事件
        self.assertIn("sse_stream_high_volume", src)
        self.assertIn("sse_stream_aborted", src)
        self.assertIn("sse_stream_done", src)

    def test_t4_telemetry_emit_works(self):
        """T4: telemetry emit 写 sse.jsonl 实测"""
        import tempfile
        import json
        tmp = Path(tempfile.mkdtemp(prefix="sse_test_"))
        os.environ["OPENCLAW_WORKSPACE"] = str(tmp)
        for m in list(sys.modules.keys()):
            if m == "core" or m.startswith("core."):
                del sys.modules[m]
        from core.telemetry import emit, new_trace
        new_trace()
        ok = emit(event="sse_stream_done",
                  fields={"sid": "test", "chunk_count": 100,
                          "disconnected": False, "aborted_reason": "",
                          "high_volume": False},
                  jsonl="sse.jsonl",
                  sid="test-sid",
                  latency_ms=1500)
        self.assertTrue(ok)
        rows = [json.loads(l) for l in (tmp / "telemetry" / "sse.jsonl").read_text().splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["event"], "sse_stream_done")
        self.assertEqual(r["chunk_count"], 100)
        self.assertEqual(r["latency_ms"], 1500)
        self.assertEqual(r["session_id"], "test-sid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
