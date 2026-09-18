# plugins/tools/shell.py — S1-C
# Shell / 后台进程 / 日志 4 件套的 plugin ABI 包装.
#
# 真正的实现留在 core/tool_dispatch.py (execute_tool + _exec_bg + list_bg_for_session
# + kill_bg_pid + tail_log 分支), 本文件只做"给 registry 看" 的门面:
#   - 让 registry.list() 里能看到 execute_shell / list_bg / kill_bg / tail_log,
#   - 让 openai_tools schema 从这一份声明生成 (跟老 lib/tools/defs.py 保持同源),
#   - 未来核心搬进插件目录时, 只改本文件 run 内部, 门面不动.
#
# 输入契约: (args: dict, ctx: dict) → dict
#   ctx.sid  会作为 sid 传入 core.execute_tool.

from __future__ import annotations
from typing import Any


async def _delegate(name: str, args: dict, ctx: dict) -> dict:
    """把调用转发给 core.tool_dispatch.execute_tool 并把 (result, artifact) 归一成 dict."""
    try:
        from core.tool_dispatch import execute_tool
    except ImportError:
        from tool_dispatch import execute_tool  # type: ignore
    sid = (ctx or {}).get("sid") or ""
    result, artifact = await execute_tool(name, args or {}, sid)
    out: dict[str, Any] = {"ok": True, "result": result}
    if artifact:
        out["artifact"] = artifact
    return out


TOOLS = [
    {
        "name": "execute_shell",
        "description": (
            "Execute a bash shell command. Auto-infers timeout. "
            "background=true → 起后台服务, 立即返回 pid + log 路径, "
            "list_bg 看进程, tail_log 看输出, kill_bg 收尾. "
            "keep_alive=true → session 结束后保留."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "command":    {"type": "string", "description": "bash command"},
                "timeout":    {"type": "integer", "description": "seconds; auto-infer if omitted"},
                "background": {"type": "boolean", "description": "起后台服务"},
                "health_url": {"type": "string",  "description": "background=true 时, 起后轮询该 URL 判断服务就绪"},
                "keep_alive": {"type": "boolean", "description": "session 结束不清理"},
            },
            "required": ["command"],
        },
        "capabilities": {"safe": False, "long_running": True},
        "run": lambda args, ctx: _delegate("execute_shell", args, ctx),
    },
    {
        "name": "list_bg",
        "description": "列出本 session 的后台进程 (pid / cmd / log 文件路径 / 存活时长).",
        "schema": {"type": "object", "properties": {}},
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("list_bg", args, ctx),
    },
    {
        "name": "kill_bg",
        "description": "杀掉后台进程. pid='all' 一次性清空本 session (可选 force=true).",
        "schema": {
            "type": "object",
            "properties": {
                "pid":   {"type": "string", "description": "PID 或 'all'"},
                "force": {"type": "boolean", "description": "强杀 (SIGKILL + 忽略 keep_alive)"},
            },
            "required": ["pid"],
        },
        "capabilities": {"safe": False},
        "run": lambda args, ctx: _delegate("kill_bg", args, ctx),
    },
    {
        "name": "tail_log",
        "description": "读文件尾部若干行 (最多 500). follow_seconds>0 → 类 tail -f, 最多守 30s.",
        "schema": {
            "type": "object",
            "properties": {
                "path":           {"type": "string",  "description": "日志文件绝对路径"},
                "lines":          {"type": "integer", "description": "尾部行数, 默认 30, 上限 500"},
                "follow_seconds": {"type": "integer", "description": "0=一次性; >0=守候秒数"},
            },
            "required": ["path"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("tail_log", args, ctx),
    },
]

VERSION = "0.1.0"
