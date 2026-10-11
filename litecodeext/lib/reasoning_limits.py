"""reasoning_limits.py — 「只思考不产出」熔断的字数阈值。

阈值跟随当前模型的 thinking_budget (token): 熔断线低于模型自己的思考预算时,
设计/推导类任务 (如逐个算 SVG 坐标) 会被当成复读循环误杀。
真正的复读由 _stream_llm_call 里的"近 8 行去重"检测负责, 不靠字数。

覆盖: config.json agent.reasoning_stall_break_chars (整数, 字符)。
"""
from __future__ import annotations

from .config import CFG, _LIVE

_CHARS_PER_TOKEN = 3
_MIN_BREAK = 7000


def stall_limits() -> tuple[int, int]:
    """返回 (提示线, 熔断线), 单位字符。提示线 = 熔断线的 5/7。"""
    override = (CFG.get("agent") or {}).get("reasoning_stall_break_chars")
    if override:
        brk = int(override)
    elif _LIVE.get("enable_thinking"):
        brk = max(_MIN_BREAK, int(_LIVE.get("thinking_budget") or 0) * _CHARS_PER_TOKEN)
    else:
        brk = _MIN_BREAK
    return brk * 5 // 7, brk


class RepeatLineDetector:
    """推理复读检测: 最近 window 个完整行里, 不同内容 ≤ max_distinct 即判复读。

    流式片段是 token 级的, 必须先拼回完整行再统计; 直接对片段 split("\\n")
    会把 `values="...;...;..."` 这类代码切成 `...` / `;` 交替的"行"而误判。
    只统计 ≤ max_len 字的非空行 (长句很少逐字重复)。"""

    def __init__(self, window: int = 8, min_lines: int = 6, max_distinct: int = 2, max_len: int = 50):
        self.window, self.min_lines, self.max_distinct, self.max_len = window, min_lines, max_distinct, max_len
        self.lines: list[str] = []
        self._buf = ""

    def feed(self, chunk: str) -> bool:
        self._buf += chunk
        *done, self._buf = self._buf.split("\n")
        for ln in done:
            s = ln.strip()
            if s and len(s) <= self.max_len:
                self.lines.append(s)
        self.lines = self.lines[-self.window:]
        return len(self.lines) >= self.min_lines and len(set(self.lines)) <= self.max_distinct

    def reset(self) -> None:
        self.lines.clear()
        self._buf = ""

    def recent(self) -> list[str]:
        return list(dict.fromkeys(self.lines))[:2]


def salvage_note(reasoning: str, head: int = 800, tail: int = 2200) -> str:
    """熔断后附给下一轮的"已想好的内容": 开头 (整体方案) + 结尾 (最新的具体决定)。
    下一轮请求里不带上一轮被掐断的推理, 不附上的话模型只能从零重想。"""
    r = (reasoning or "").strip()
    if not r:
        return ""
    body = r if len(r) <= head + tail else r[:head] + "\n…(中间省略)…\n" + r[-tail:]
    return ("\n\n下面是你上一轮已经想好的内容, 直接据此动手, 不要从头重新推导:\n"
            "<<<\n" + body + "\n>>>")
