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



def done_then_tail_producer(tail=0.3):
    """模拟 agent_stream: 先发 [DONE], 之后还要收尾 (落库) 一段时间才真正结束。"""
    def factory():
        async def gen():
            yield "data: hi\n\n"
            yield "data: [DONE]\n\n"
            await asyncio.sleep(tail)
        return gen()
    return factory


class TestFinishingWindow(unittest.TestCase):
    """2026-10-16 深度长跑 S6: 客户端读到 [DONE] 立刻发下一条, 撞上收尾窗口被 409。"""

    def test_done_marks_finishing_and_wait_returns_after_tail(self):
        async def main():
            reg = RunRegistry()
            r = reg.start("s", done_then_tail_producer(0.3))
            while not r.finishing:
                await asyncio.sleep(0.01)
            self.assertTrue(reg.running("s"))           # 收尾中仍算占用, 不能抢跑
            finished = await r.wait_finished(timeout=2)
            return finished, reg.running("s")
        finished, running = run(main())
        self.assertTrue(finished)
        self.assertFalse(running)

    def test_still_generating_is_not_finishing(self):
        async def main():
            reg = RunRegistry()
            r = reg.start("s", slow_producer(20, gap=0.05))
            await asyncio.sleep(0.1)
            state = (r.finishing, await r.wait_finished(timeout=0.05))
            await r.task
            return state
        finishing, finished = run(main())
        self.assertFalse(finishing)                     # 还在生成: 不进入等待, 入口照旧 409
        self.assertFalse(finished)                      # 超时返回 False, 不会无限等



if __name__ == "__main__":
    unittest.main()
