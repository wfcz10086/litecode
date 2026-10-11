"""tool_result_eviction.py — 旧工具结果淘汰, 按批次推进以保住 KV 前缀缓存。

本地后端 (ollama / llama.cpp) 只能复用与上一次请求完全相同的前缀。
旧实现每轮把"倒数第 keep_recent+1 条"截短: 截断点每轮前移一格,
前缀在该处变化, 其后的全部历史都要重新预填。

这里截断点只按 batch 整步推进: 新增工具结果没满一批时, 发出的历史
与上一轮逐字相同。纯函数, 不改传入的消息 (会话缓存里的 dict 是共享的)。
"""
from __future__ import annotations

_SHORT = 200   # 不超过这个长度的结果不值得截


def _content_len(m: dict) -> int:
    c = m.get("content")
    return len(c) if isinstance(c, str) else 0


def eviction_count(sizes: list[int], keep_recent: int, batch: int, max_total: int) -> int:
    """sizes 是按时间顺序的各工具结果长度, 返回要截掉最旧的几条 (batch 的整数倍或全部)。"""
    n = len(sizes)
    if n <= keep_recent:
        k = 0
    else:
        k = (n - keep_recent) // batch * batch
    while k < n and sum(sizes[k:]) > max_total:
        k = min(n, k + batch)
    return k


def _tool_name_of(messages: list, idx: int) -> str:
    call_id = messages[idx].get("tool_call_id", "")
    if not call_id:
        return ""
    for j in range(idx - 1, max(idx - 5, -1), -1):
        for tc in messages[j].get("tool_calls") or []:
            if tc.get("id") == call_id:
                return (tc.get("function") or {}).get("name", "")
    return ""


def clear_old_tool_results(messages: list, keep_recent: int = 6, max_total_chars: int = 15000,
                           batch: int = 8) -> list:
    """返回新列表; 被截的消息是副本, 其余消息原样复用。"""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    k = eviction_count([_content_len(messages[i]) for i in tool_idx], keep_recent, batch, max_total_chars)
    if not k:
        return list(messages)
    out = list(messages)
    for idx in tool_idx[:k]:
        content = out[idx].get("content")
        if not isinstance(content, str) or len(content) <= _SHORT:
            continue
        out[idx] = {**out[idx],
                    "content": f"[cleared:{_tool_name_of(out, idx) or '?'} {len(content)}c] {content[:50]}..."}
    return out


def strip_old_reasoning(messages: list, keep: int = 1) -> list:
    """只保留最近 keep 条 assistant 的 reasoning_content, 更早的置空 (不删键, DeepSeek 要求字段存在)。
    返回新列表, 不改传入的消息。"""
    out = list(messages)
    kept = 0
    for i in range(len(out) - 1, -1, -1):
        m = out[i]
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            if kept < keep:
                kept += 1
            else:
                out[i] = {**m, "reasoning_content": ""}
    return out
