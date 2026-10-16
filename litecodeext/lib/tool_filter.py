"""lib/tool_filter.py — 单次请求禁用部分工具 (请求体 disabled_tools)。

用途: 评测时断网 (SWE-bench 不许上网查官方 PR), 以后也能做"离线模式"。
被禁的工具不发给模型; 模型硬调也在派发前拦下。
"web" 是组名, 展开成全部联网工具。
"""
from __future__ import annotations

GROUPS = {
    "web": frozenset({
        "web_fetch", "web_search", "browser_act", "browser_login", "browser_read", "browser_search",
        "browser_snapshot", "github_search_issues", "stackoverflow_search", "search_code_error",
    }),
}


def parse(raw) -> frozenset:
    """请求体里的 disabled_tools (list 或逗号分隔字符串) → 工具名集合, 组名展开。"""
    if not raw:
        return frozenset()
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    out = set()
    for x in items:
        x = str(x).strip()
        if x:
            out |= GROUPS.get(x, {x})
    return frozenset(out)


def visible(tool_defs, disabled: frozenset):
    """发给模型的工具列表。没禁用就原样返回同一个对象 (保 KV 前缀缓存)。"""
    if not disabled:
        return tool_defs
    return [t for t in tool_defs if t["function"]["name"] not in disabled]


def blocked_msg(name: str) -> str:
    return f"ERROR: 工具 {name} 在本次任务中已被禁用, 请只用本地代码和测试完成。"
