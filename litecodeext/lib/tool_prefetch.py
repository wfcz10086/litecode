"""tool_prefetch.py — 同一批工具调用里, 开头连续的只读调用提前并发执行。

模型一次发出 "读 3 个文件 + 搜 2 次" 时, 主循环原本逐个 await。这里在循环开始前
把批次 *开头* 的只读调用 (遇到第一个非只读调用就停, 保证不会读到写之前的旧内容)
一起启动; 主循环照旧逐个走守卫和后处理, 轮到执行时直接取已在跑的任务。

被守卫拦下的预取结果直接丢弃 —— 只读调用没有副作用, 多跑一次无害。
开关: config.json agent.parallel_readonly_tools (默认开)。
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

READ_ONLY = frozenset({
    "read_file", "get_tree", "find_files", "search_code", "find_symbol", "locate_symbol",
    "web_fetch", "web_search", "github_search_issues", "stackoverflow_search", "search_code_error",
    "tail_log", "list_skills", "git_status", "git_diff", "git_log",
    "list_bg", "list_timers", "snapshot_list", "task_list", "ssh_list_hosts",
})


def plan(tool_calls: list, parse: Callable) -> list[tuple[str, str, dict]]:
    """返回可预取的 [(tool_call_id, name, args)]; 不足 2 个时返回空 (没得并发)。"""
    out = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        name = fn.get("name", "")
        if name not in READ_ONLY or not tc.get("id"):
            break
        args, err = parse(fn.get("arguments"), name)
        if err:
            break
        out.append((tc["id"], name, args))
    return out if len(out) >= 2 else []


class Prefetcher:
    def __init__(self) -> None:
        self._tasks: dict[str, tuple[dict, asyncio.Future]] = {}

    @classmethod
    def start(cls, tool_calls: list, parse: Callable,
              run: Callable[[str, dict], Awaitable], enabled: bool = True) -> "Prefetcher":
        p = cls()
        if enabled:
            for tc_id, name, args in plan(tool_calls, parse):
                p._tasks[tc_id] = (args, asyncio.ensure_future(run(name, args)))
        return p

    def __len__(self) -> int:
        return len(self._tasks)

    def take(self, tc_id: str, args: dict):
        """取出预取任务; 参数对不上 (被改写过) 时返回 None, 由调用方现跑。"""
        item = self._tasks.pop(tc_id, None)
        if item is None:
            return None
        pre_args, task = item
        if pre_args != args:
            task.cancel()
            return None
        return task

    def cancel_rest(self) -> None:
        for _, task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
