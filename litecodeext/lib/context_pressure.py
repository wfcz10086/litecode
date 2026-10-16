"""lib/context_pressure.py — 当前 prompt 占多少 token, 给上下文裁剪判断"有没有压力"用。

有上游 usage 锚点: 锚点真值 + 锚点之后新增消息的估算 (真值自带系统提示词和工具定义);
没有锚点: 历史估算 + 固定开销 (系统提示词 + 工具定义, 见 overhead_calib)。
"""
from __future__ import annotations

from typing import Callable


def history_est(messages: list, est: Callable[[object], int]) -> int:
    return sum(est(m.get("content", "")) + est(m.get("tool_calls", "")) + est(m.get("reasoning_content", ""))
               for m in messages)


def prompt_tokens(messages: list, usage, est: Callable[[object], int]) -> int:
    h = history_est(messages, est)
    if usage.usage_reports > 0 and usage.last_real_prompt_tokens >= usage.last_est_at_anchor:
        return usage.last_real_prompt_tokens + max(0, h - usage.last_est_at_anchor)
    return h + int(getattr(usage, "fixed_overhead", 0) or 0)


def under_pressure(messages: list, usage, est: Callable[[object], int], budget: int, ratio: float) -> bool:
    """当前 prompt >= budget*ratio 才算有压力。"""
    return prompt_tokens(messages, usage, est) >= budget * ratio
