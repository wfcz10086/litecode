"""lib/overhead_calib.py — 固定开销 (系统提示词 + 工具定义) 的 token 校准。

est_tokens 对工具 JSON schema 按 3 字符/token 算, 实测高估约 3 倍 (估 4.4 万, 上游实报约 1.3 万)。
每回合第一次请求还没有上游 usage 锚点, 预算 = 窗口*pct - 固定开销, 高估会把预算压到 4096 下限,
于是一开局就把十几条历史删掉 (2026-10-12 wx-xxxx 实测 removed 11/13 msgs, 36616t/4096t)。

做法: 每次上游回报 usage, 记下 "真实 prompt_tokens - 对话历史估算" 作为该模型的固定开销实测值;
之后的回合直接用这个值。进程刚起还没学到时, 估算值最多算预算的一半, 不让它把历史额度吃光。
"""
from __future__ import annotations

_SEEN: dict[str, int] = {}


def learn(model: str, real_prompt: int, est_history: int) -> None:
    """上游回报 usage 时调用。差值不合理 (负数 / 超过总量) 就忽略。"""
    try:
        x = int(real_prompt) - int(est_history)
    except (TypeError, ValueError):
        return
    if 0 < x < int(real_prompt):
        _SEEN[model or ""] = x


def adjust(model: str, raw_est: int, budget_total: int) -> int:
    """返回本回合该用的固定开销 t。"""
    seen = _SEEN.get(model or "")
    if seen:
        return seen
    return min(int(raw_est), int(budget_total) // 2)


def reset() -> None:
    _SEEN.clear()
