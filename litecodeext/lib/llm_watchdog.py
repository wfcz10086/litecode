"""lib/llm_watchdog.py — 模型服务卡死自愈。

ollama 偶发排队计数泄漏 / 调度卡死: 之后每个请求都回 503 "maximum pending requests
exceeded" 或一直不回, 只有重启 ollama 才好。以前要人工 ssh 上去重启, 这里自动做:

    transport 每次看到"排队满"或读超时 → note_busy(url)
    window_s 内攒够 threshold 次 → 先探活确认 (探活成功说明只是真忙, 不动)
    → 探活也失败 → ssh 执行 restart_cmd, cooldown_s 内不再重启
    攒够次数起 degrade_s 秒内 (以及重启后模型加载期间) 请求临时改走 fallback_model_id,
    到期后自动回到主模型。

config.json:
    "llm_watchdog": {
        "enabled": true,
        "match": "your-host-a.example:11434",      # backend_url 包含它才管
        "probe_url": "http://your-host-a.example:11434",
        "probe_model": "",                   # 空 = 用当前模型
        "ssh": {"host": "...", "port": 22, "user": "...", "password": "..."},
        "restart_cmd": "echo PASS | sudo -S systemctl restart ollama",
        "fallback_model_id": "Qwen3.6-35B",  # 降级期间改走的模型 (config.models 里的 id), 空 = 不切
        "threshold": 3, "window_s": 180, "cooldown_s": 900, "degrade_s": 120
    }
没配或 enabled=false 时所有函数都是空操作。
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

BUSY_MARKERS = ("maximum pending", "server busy")


def is_busy_body(text) -> bool:
    s = text.decode("utf-8", "replace") if isinstance(text, (bytes, bytearray)) else str(text or "")
    s = s.lower()
    return any(m in s for m in BUSY_MARKERS)


class Watchdog:
    """纯状态机; 探活 / 重启 / 时钟都可注入, 便于单测。"""

    def __init__(self, cfg: dict, probe: Optional[Callable[[], Awaitable[bool]]],
                 restart: Optional[Callable[[], Awaitable[str]]], clock: Callable[[], float] = time.time,
                 log=None):
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled"))
        self.match = str(self.cfg.get("match") or "")
        self.threshold = int(self.cfg.get("threshold", 3))
        self.window_s = float(self.cfg.get("window_s", 180))
        self.cooldown_s = float(self.cfg.get("cooldown_s", 900))
        self.degrade_s = float(self.cfg.get("degrade_s", 120))
        self.fallback_id = str(self.cfg.get("fallback_model_id") or "")
        self.degraded_until = 0.0
        self._probe, self._restart, self._clock, self._log = probe, restart, clock, log
        self._hits: list[float] = []
        self._task: Optional[asyncio.Task] = None
        self.last_restart = 0.0       # 上次重启时刻 (0 = 没重启过)
        self.last_result = ""

    def _mine(self, url: str) -> bool:
        return self.enabled and (not self.match or self.match in (url or ""))

    def restarting(self) -> bool:
        """正在探活/重启, 或刚重启完 90 秒内 (模型还在加载)。"""
        if self._task and not self._task.done():
            return True
        return bool(self.last_restart) and self._clock() - self.last_restart < 90

    def degraded(self, url: str) -> bool:
        """主模型此刻不可用, 该改走 fallback。"""
        return self._mine(url) and (self.restarting() or self._clock() < self.degraded_until)

    def note_ok(self, url: str) -> None:
        if self._mine(url):
            self._hits.clear()
            self.degraded_until = 0.0

    def note_busy(self, url: str) -> bool:
        """记一次异常; 返回 True 表示这次触发了自愈流程。"""
        if not self._mine(url):
            return False
        now = self._clock()
        self._hits = [t for t in self._hits if now - t < self.window_s] + [now]
        if len(self._hits) < self.threshold or self.restarting():
            return False
        self.degraded_until = now + self.degrade_s
        if self.last_restart and now - self.last_restart < self.cooldown_s:
            return False
        self._hits.clear()
        self._task = asyncio.ensure_future(self._heal())
        return True

    async def _heal(self) -> None:
        if not (self._probe and self._restart and self.cfg.get("restart_cmd")):
            return                    # 只配了降级没配重启
        try:
            if await self._probe():
                self._say("探活正常, 只是真忙, 不重启")
                return
            self.last_restart = self._clock()
            self.last_result = await self._restart()
            self._say(f"已重启模型服务: {self.last_result[:200]}")
        except Exception as e:
            self.last_result = f"ERROR {type(e).__name__}: {e}"
            self._say(f"自愈失败: {self.last_result}")

    def _say(self, msg: str) -> None:
        if self._log:
            self._log.warning(f"[llm-watchdog] {msg}")


# ── 进程内单例 (按 config.json 装配) ─────────────────────────
_WD: Optional[Watchdog] = None


def _build() -> Watchdog:
    from .config import CFG, _LIVE, log, wire_model_name
    cfg = CFG.get("llm_watchdog") or {}

    async def probe() -> bool:
        import httpx
        base = str(cfg.get("probe_url") or "").rstrip("/")
        if not base:
            return False
        model = cfg.get("probe_model") or wire_model_name(_LIVE.get("model_id", ""))
        body = {"model": model, "prompt": "hi", "stream": False, "options": {"num_predict": 1}}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as c:
                r = await c.post(f"{base}/api/generate", json=body)
                return r.status_code == 200
        except Exception:
            return False

    async def restart() -> str:
        try:
            from core.tools.handlers.remote import _ssh
        except ImportError:
            from tools.handlers.remote import _ssh  # type: ignore  # core/ 在 sys.path 上时
        s = cfg.get("ssh") or {}
        rc, out, err = await _ssh(s.get("host", ""), int(s.get("port") or 22), s.get("user", "root"),
                                  s.get("password", ""), cfg["restart_cmd"], 60, s.get("key_file", ""))
        return f"rc={rc} {(out or '').strip()} {(err or '').strip()}".strip()

    return Watchdog(cfg, probe, restart, log=log)


def get() -> Watchdog:
    global _WD
    if _WD is None:
        try:
            _WD = _build()
        except Exception:
            _WD = Watchdog({}, None, None)
    return _WD


def note_busy(url: str) -> bool:
    try:
        return get().note_busy(url)
    except Exception:
        return False


def note_ok(url: str) -> None:
    try:
        get().note_ok(url)
    except Exception:
        pass


def fallback_for(url: str) -> Optional[dict]:
    """主模型降级中 → 返回 fallback 模型的 config 条目 (含 id/backend_url/api_key), 否则 None。"""
    try:
        wd = get()
        if not (wd.fallback_id and wd.degraded(url)):
            return None
        from .config import CFG
        for m in CFG.get("models", []) or []:
            if m.get("id") == wd.fallback_id and m.get("backend_url"):
                return m
    except Exception:
        pass
    return None


def restarting() -> bool:
    try:
        return get().restarting()
    except Exception:
        return False
