"""chat_runs.py — Web 对话轮次与浏览器连接解耦。

问题 (2026-10-16 实测): /api/chat 的 SSE 直接绑在浏览器请求上, 前端一断 (刷新 /
切会话 / 关页 / 网络抖) web_ui 就关掉上游流, server 端 agent 随之停在当前迭代;
别的标签页 / 别人打开同一会话也看不到进行中的回复。

做法: 每轮对话由后台 task 消费上游, 帧写进内存缓冲; 浏览器只是"订阅者",
可以从任意偏移重连回放 + 继续实时跟随。订阅者断开不影响本轮执行。

纯 asyncio, 不依赖 web_ui, 可单测。
"""
import asyncio
import time
from typing import AsyncIterator, Callable, Optional

KEEP_FINISHED_S = 600      # 结束后缓冲保留 10 分钟, 供晚到的订阅者回放


class Run:
    def __init__(self, sid: str):
        self.sid = sid
        self.frames: list[str] = []
        self.done = False
        self.started = time.time()
        self.ended: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self.finishing = False   # 已发出 [DONE], 只剩收尾 (server 落库 / web 侧保存)
        self._cond = asyncio.Condition()

    async def _push(self, frame: str):
        self.frames.append(frame)
        if "[DONE]" in frame:
            self.finishing = True
        async with self._cond:
            self._cond.notify_all()

    async def _finish(self):
        self.done = True
        self.ended = time.time()
        async with self._cond:
            self._cond.notify_all()

    async def wait_finished(self, timeout: float) -> bool:
        """等本轮彻底结束 (条件变量唤醒, 不轮询); 返回是否已结束。"""
        async with self._cond:
            try:
                await asyncio.wait_for(self._cond.wait_for(lambda: self.done), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        return self.done

    def status(self) -> dict:
        result = {"running": not self.done, "frames": len(self.frames),
                  "started": self.started, "ended": self.ended}
        if hasattr(self, 'user_ts'):
            result['user_ts'] = self.user_ts
        return result

    async def follow(self, start: int = 0, idle: float = 15.0) -> AsyncIterator[Optional[str]]:
        """回放 start 之后的帧并实时跟随, 直到本轮结束。
        idle 秒内无新帧时 yield None, 调用方借此发心跳 / 探测断连。"""
        i = max(0, start)
        while True:
            while i < len(self.frames):
                yield self.frames[i]
                i += 1
            if self.done:
                return
            async with self._cond:
                if i < len(self.frames) or self.done:
                    continue
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=idle)
                except asyncio.TimeoutError:
                    yield None


class RunRegistry:
    def __init__(self, keep_finished_s: float = KEEP_FINISHED_S):
        self._runs: dict[str, Run] = {}
        self._keep = keep_finished_s

    def get(self, sid: str) -> Optional[Run]:
        self._gc()
        return self._runs.get(sid)

    def running(self, sid: str) -> bool:
        r = self.get(sid)
        return bool(r and not r.done)

    def start(self, sid: str, producer: Callable[[], AsyncIterator[str]]) -> Run:
        """起一个后台轮次; 同会话已有进行中的轮次 → RuntimeError。"""
        if self.running(sid):
            raise RuntimeError(f"session {sid} already has a running turn")
        run = Run(sid)

        async def _drive():
            try:
                async for frame in producer():
                    await run._push(frame)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # producer 自己该兜底; 这里防漏网
                import json
                from lib.friendly_errors import classify   # [trace] 附友好错误字段, 老格式 error 字符串不变
                await run._push(f"data: {json.dumps({'error': str(e), 'friendly': classify(e)}, ensure_ascii=False)}\n\n")
            finally:
                await run._finish()

        self._runs[sid] = run
        run.task = asyncio.ensure_future(_drive())
        return run

    def _gc(self):
        now = time.time()
        for sid in [s for s, r in self._runs.items()
                    if r.done and r.ended and now - r.ended > self._keep]:
            self._runs.pop(sid, None)


REGISTRY = RunRegistry()
