# plugins/tools/guard.py — 工具重复调用死循环拦截器 (S1-D 抽出)
#
# 用户场景 (litecode_server.py 原注释):
#   模型反复调用同 URL web_fetch / 同 query web_search 都失败
#   或同形态 sed/grep/curl execute_shell 微调行号疯狂跑 (钓鱼游戏案例)
# 处理:
#   同一 (tool, 关键参数) 签名第 3 次出现时, 该次工具调用被拦截,
#   返回 SYSTEM 警告给模型, 让它换路子.
#
# 写文件类不在此处拦 (有独立的 batch_write_guard);
# spawn_agent 不拦 (子代理可能合法多发).
#
# 用法 (litecode_server 层每个 turn 开新实例):
#   from plugins.tools.guard import ToolLoopGuard
#   guard = ToolLoopGuard()
#   for tc in tool_call_list:
#       blocked, warn = guard.check(fn_name, fn_args)
#       if blocked:
#           tool_results.append({"role": "tool", "tool_call_id": tc["id"], "content": warn})
#           continue
#       ...  # 正常派发

from __future__ import annotations
from collections import deque
from typing import Optional
import json as _json
import re as _re


# 豁免名单: 这些工具允许高频重复
EXEMPT_FROM_LOOP_GUARD = frozenset({
    "write_file", "patch_file", "create_file",  # 有独立 batch_write_guard
    "spawn_agent",                                # 子代理合法多发
})

DEFAULT_LIMIT = 3
DEFAULT_HISTORY_MAXLEN = 12


def compute_sig(fn: str, args: dict) -> str:
    """算 (tool, 关键参数) 签名 — 用于检测重复调用."""
    try:
        if fn in ("web_fetch", "browser_fetch", "url_to_pdf"):
            return f"{fn}::{(args.get('url') or '')[:160]}"
        if fn in ("web_search", "browser_search", "deep_search"):
            q = args.get("query") or args.get("q") or ""
            return f"{fn}::{q[:120]}"
        if fn in ("read_file", "open_file"):
            return f"{fn}::{args.get('filepath') or args.get('path', '')}"
        if fn in ("write_file", "patch_file", "create_file"):
            # 写文件:同一文件多次写不一定是死循环 (可能在补内容),
            # 这里只签 filepath. 实际豁免上面已挡, 保留分支便于测试.
            return f"{fn}::{args.get('filepath') or args.get('path', '')}"
        if fn in ("execute_shell", "execute_python", "run_python"):
            # 模型经常 sed -n '218,225p' → '219,223p' 微调行号疯狂跑,
            # 把所有连续数字替换 <N>, sig 折叠相同形态命令.
            cmd = (args.get("command") or args.get("cmd") or args.get("code") or "")[:300]
            norm = _re.sub(r"\d+", "<N>", cmd)
            return f"{fn}::{norm[:160]}"
        # 其他工具用全 args sig
        return f"{fn}::{_json.dumps(args, sort_keys=True, ensure_ascii=False)[:200]}"
    except Exception:
        return f"{fn}::?"


def raw_display(fn: str, args: dict) -> str:
    """给模型展示的真实调用参数 (未归一化) — 判重用 compute_sig(), 展示用这个.
    模型必须认得出这是它自己刚打的命令, 否则封禁提示等于白发."""
    try:
        if fn in ("web_fetch", "browser_fetch", "url_to_pdf"):
            return (args.get("url") or "")[:160]
        if fn in ("web_search", "browser_search", "deep_search"):
            return (args.get("query") or args.get("q") or "")[:120]
        if fn in ("read_file", "open_file", "write_file", "patch_file", "create_file"):
            return args.get("filepath") or args.get("path", "")
        if fn in ("execute_shell", "execute_python", "run_python"):
            return (args.get("command") or args.get("cmd") or args.get("code") or "")[:160]
        return _json.dumps(args, sort_keys=True, ensure_ascii=False)[:200]
    except Exception:
        return "?"


def _warn_first(fn: str, key: str, count: int) -> str:
    return (
        f"[SYSTEM-LOOP-GUARD] 工具 `{fn}` 用相同参数 `{key}` 已经调用了 {count} 次, "
        f"显然这条路走不通 (爬不到/搜不到/被反爬). 禁止再次调用相同的 (工具, 参数). \n"
        f"现在你必须二选一: \n"
        f"  (A) 换关键词/换 URL/换工具 (例如 web_search → web_fetch 别的源, 或 deep_search)\n"
        f"  (B) 直接基于已有信息和你的知识储备回答用户, 在回答里说明 '某项资料未取到所以使用通用知识'\n"
        f"⚠️ 严禁再次调用 `{fn}({key})`."
    )


def _warn_repeat(fn: str, key: str) -> str:
    return f"[SYSTEM-LOOP-GUARD] ⛔ `{fn}({key})` 已被永久封禁本轮对话，禁止继续重试。立即换方案。"


class ToolLoopGuard:
    """每次用户 turn 新建一份 — 状态不跨 turn."""

    def __init__(self, *, limit: int = DEFAULT_LIMIT, history_maxlen: int = DEFAULT_HISTORY_MAXLEN):
        self.limit = limit
        self.history: deque[str] = deque(maxlen=history_maxlen)
        self.break_sent: set[str] = set()
        self.block_count = 0  # 本轮 (整个 turn) 累计拦截次数, 供上层熔断判断

    def check(self, fn: str, args: dict) -> tuple[bool, Optional[str], str]:
        """
        Returns:
            (blocked, warn_message_or_None, sig)

        blocked=True  → 上层直接把 warn 塞给模型 (tool result), 跳过实际派发
        blocked=False → 上层继续正常派发
        """
        if fn in EXEMPT_FROM_LOOP_GUARD:
            return False, None, ""
        sig = compute_sig(fn, args)
        self.history.append(sig)
        count = sum(1 for s in self.history if s == sig)
        if count < self.limit:
            return False, None, sig
        # 展示给模型的是它实际打的命令 (raw_display), 判重仍用归一化 sig — 两者分开
        key = raw_display(fn, args)[:120]
        self.block_count += 1
        if sig not in self.break_sent:
            self.break_sent.add(sig)
            return True, _warn_first(fn, key, count), sig
        return True, _warn_repeat(fn, key), sig
