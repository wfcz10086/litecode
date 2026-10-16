"""lib/chat_runs.py: 浏览器断开不能停掉对话轮次; 刷新 / 别的标签页能回放 + 跟随。

实例 (2026-10-16): 三步任务发出后 25s 断开 curl, server 在断开那一刻就结束本轮, 后两步没跑。
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.chat_runs import RunRegistry  # noqa: E402


def slow_producer(n, gap=0.02, log=None):
    def factory():
        async def gen():
            for i in range(n):
                await asyncio.sleep(gap)
                if log is not None:
                    log.append(i)
                yield f"data: {i}\n\n"
        return gen()
    return factory


def run(coro):
    return asyncio.run(coro)


class TestChatRuns(unittest.TestCase):
    def test_subscriber_leaving_does_not_stop_turn(self):
        async def main():
            reg, log = RunRegistry(), []
            r = reg.start("s1", slow_producer(10, log=log))
            async for f in r.follow(0):
                break                       # 浏览器断开 / 切走
            await r.task
            return r, log
        r, log = run(main())
        self.assertEqual(log, list(range(10)))
        self.assertTrue(r.done)
        self.assertEqual(len(r.frames), 10)

    def test_late_subscriber_replays_then_follows(self):
        async def main():
            reg = RunRegistry()
            r = reg.start("s1", slow_producer(8))
            await asyncio.sleep(0.09)       # 已产出几帧后才重连
            got = [f async for f in r.follow(0) if f is not None]
            tail = [f async for f in r.follow(5) if f is not None]
            return got, tail
        got, tail = run(main())
        self.assertEqual(got, [f"data: {i}\n\n" for i in range(8)])
        self.assertEqual(tail, [f"data: {i}\n\n" for i in range(5, 8)])

    def test_two_viewers_see_same_frames(self):
        async def main():
            reg = RunRegistry()
            r = reg.start("s1", slow_producer(6))
            async def view():
                return [f async for f in r.follow(0) if f is not None]
            return await asyncio.gather(view(), view())
        a, b = run(main())
        self.assertEqual(a, b)
        self.assertEqual(len(a), 6)

    def test_second_turn_rejected_while_running(self):
        async def main():
            reg = RunRegistry()
            r = reg.start("s1", slow_producer(3))
            with self.assertRaises(RuntimeError):
                reg.start("s1", slow_producer(1))
            self.assertTrue(reg.running("s1"))
            await r.task
            self.assertFalse(reg.running("s1"))
            reg.start("s1", slow_producer(1))   # 结束后可以开下一轮
        run(main())

    def test_idle_yields_heartbeat_marker(self):
        async def main():
            reg = RunRegistry()
            r = reg.start("s1", slow_producer(2, gap=0.15))
            return [f async for f in r.follow(0, idle=0.05)]
        frames = run(main())
        self.assertIn(None, frames)
        self.assertEqual([f for f in frames if f is not None], ["data: 0\n\n", "data: 1\n\n"])

    def test_producer_crash_becomes_error_frame(self):
        def bad():
            async def gen():
                yield "data: 0\n\n"
                raise ValueError("boom")
            return gen()
        async def main():
            reg = RunRegistry()
            r = reg.start("s1", bad)
            return [f async for f in r.follow(0) if f is not None], r
        frames, r = run(main())
        self.assertTrue(r.done)
        self.assertIn("boom", frames[-1])

    def test_finished_runs_expire(self):
        async def main():
            reg = RunRegistry(keep_finished_s=0)
            r = reg.start("s1", slow_producer(1))
            await r.task
            await asyncio.sleep(0.01)
            return reg.get("s1")
        self.assertIsNone(run(main()))


if __name__ == "__main__":
    unittest.main()
