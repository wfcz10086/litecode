"""lib/llm_watchdog.py: 排队满攒够次数 → 探活 → 重启 / 降级切 fallback。"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.llm_watchdog import Watchdog, is_busy_body  # noqa: E402

URL = "http://your-host-a.example:11434/v1"
BUSY = b'{"error":{"message":"server busy, please try again.  maximum pending requests exceeded"}}'


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(probe_ok=False, **over):
    calls = {"probe": 0, "restart": 0}

    async def probe():
        calls["probe"] += 1
        return probe_ok

    async def restart():
        calls["restart"] += 1
        return "rc=0"

    cfg = {"enabled": True, "match": "your-host-a.example:11434", "restart_cmd": "systemctl restart ollama",
           "fallback_model_id": "Qwen3.6-35B", "threshold": 3, "window_s": 180, "cooldown_s": 900,
           "degrade_s": 120, **over}
    clk = Clock()
    return Watchdog(cfg, probe, restart, clock=clk), clk, calls


async def settle(wd):
    if wd._task:
        await wd._task


class TestWatchdog(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_busy_body(self):
        self.assertTrue(is_busy_body(BUSY))
        self.assertFalse(is_busy_body(b'{"error":"model not found"}'))

    def test_restart_after_threshold(self):
        async def go():
            wd, clk, calls = make()
            self.assertFalse(wd.note_busy(URL))
            self.assertFalse(wd.note_busy(URL))
            self.assertFalse(wd.degraded(URL))
            self.assertTrue(wd.note_busy(URL))
            self.assertTrue(wd.degraded(URL))          # 探活/重启期间就切 fallback
            await settle(wd)
            self.assertEqual(calls, {"probe": 1, "restart": 1})
            self.assertTrue(wd.restarting())           # 重启后 90 秒模型加载期
            clk.t += 200
            self.assertFalse(wd.degraded(URL))         # 过了降级期回主模型
        self.run_async(go())

    def test_probe_ok_means_just_busy(self):
        async def go():
            wd, clk, calls = make(probe_ok=True)
            for _ in range(3):
                wd.note_busy(URL)
            await settle(wd)
            self.assertEqual(calls, {"probe": 1, "restart": 0})
            self.assertTrue(wd.degraded(URL))          # 不重启, 但降级期内照样分流
            clk.t += 121
            self.assertFalse(wd.degraded(URL))
        self.run_async(go())

    def test_cooldown(self):
        async def go():
            wd, clk, calls = make()
            for _ in range(3):
                wd.note_busy(URL)
            await settle(wd)
            clk.t += 300
            for _ in range(3):
                self.assertFalse(wd.note_busy(URL))    # 15 分钟内不再重启
            clk.t += 700
            for _ in range(3):
                wd.note_busy(URL)
            await settle(wd)
            self.assertEqual(calls["restart"], 2)
        self.run_async(go())

    def test_window_and_ok_reset(self):
        async def go():
            wd, clk, calls = make()
            wd.note_busy(URL)
            wd.note_busy(URL)
            clk.t += 200                               # 超出窗口, 旧的不算
            self.assertFalse(wd.note_busy(URL))
            wd.note_busy(URL)
            wd.note_ok(URL)                            # 一次成功清零
            self.assertFalse(wd.note_busy(URL))
            self.assertEqual(calls["probe"], 0)
        self.run_async(go())

    def test_other_backend_and_disabled(self):
        async def go():
            wd, _, calls = make()
            for _ in range(5):
                self.assertFalse(wd.note_busy("https://api.deepseek.com/v1"))
            wd2, _, calls2 = make(enabled=False)
            for _ in range(5):
                self.assertFalse(wd2.note_busy(URL))
            self.assertFalse(wd2.degraded(URL))
            self.assertEqual(calls["probe"] + calls2["probe"], 0)
        self.run_async(go())

    def test_fallback_only_without_restart_cmd(self):
        async def go():
            wd, _, calls = make(restart_cmd="")
            for _ in range(3):
                wd.note_busy(URL)
            await settle(wd)
            self.assertTrue(wd.degraded(URL))
            self.assertEqual(calls, {"probe": 0, "restart": 0})
        self.run_async(go())


if __name__ == "__main__":
    unittest.main()
