# plugins/tools/file_code.py — S1-B
# 文件 + 代码 8 件套的 plugin ABI 包装.
#
# 真正实现留在 core/tool_dispatch.py (每个 name 一个 elif 分支), 本文件只做门面:
#   - 让 registry 看得到 read_file / write_file / patch_file / apply_blocks /
#     get_tree / find_files / search_code / find_symbol,
#   - OpenAI schema 从这一份声明生成 (跟 lib/tools/defs.py 同源),
#   - 后续搬 core 实现进插件目录时, 只改 run 内部, 门面不动.

from __future__ import annotations
from typing import Any


async def _delegate(name: str, args: dict, ctx: dict) -> dict:
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
        "name": "read_file",
        "description": (
            "Read file content. Use lines='start:end' (colon separator) to read a range."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "lines":    {"type": "string", "description": "Line range 'start:end', 用冒号不用短横"},
            },
            "required": ["filepath"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("read_file", args, ctx),
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file.",
        "schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "content":  {"type": "string"},
            },
            "required": ["filepath", "content"],
        },
        "capabilities": {"safe": False},
        "run": lambda args, ctx: _delegate("write_file", args, ctx),
    },
    {
        "name": "patch_file",
        "description": "Atomic unique-string replace. old_str must appear exactly once.",
        "schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "old_str":  {"type": "string"},
                "new_str":  {"type": "string"},
                "append":   {"type": "boolean"},
            },
            "required": ["filepath"],
        },
        "capabilities": {"safe": False},
        "run": lambda args, ctx: _delegate("patch_file", args, ctx),
    },
    {
        "name": "apply_blocks",
        "description": (
            "Aider-style SEARCH/REPLACE 多块一次性 apply, 比多次 patch_file 高效."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "blocks": {"type": "string", "description": "拼接的 aider blocks 文本"},
            },
            "required": ["blocks"],
        },
        "capabilities": {"safe": False},
        "run": lambda args, ctx: _delegate("apply_blocks", args, ctx),
    },
    {
        "name": "get_tree",
        "description": (
            "目录结构快照. 自动跳 __pycache__ / node_modules / .git / venv / dist / build."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "path":        {"type": "string",  "description": "根目录 (默认 workspace)"},
                "max_depth":   {"type": "integer", "description": "深度 (默认 3)"},
                "show_hidden": {"type": "boolean", "description": "含隐藏文件 (默认 false)"},
            },
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("get_tree", args, ctx),
    },
    {
        "name": "find_files",
        "description": "按 glob 找文件. 例 pattern='*.py' / '**/*test*'.",
        "schema": {
            "type": "object",
            "properties": {
                "pattern":     {"type": "string"},
                "path":        {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["pattern"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("find_files", args, ctx),
    },
    {
        "name": "search_code",
        "description": "全项目 grep. 返回 file:line:content.",
        "schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string"},
                "path":        {"type": "string"},
                "ext":         {"type": "string", "description": "扩展名过滤, 如 .py"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("search_code", args, ctx),
    },
    {
        "name": "find_symbol",
        "description": "从 SYMBOL_INDEX.json 精确查函数/类定义, 比 grep 快.",
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("find_symbol", args, ctx),
    },
]

VERSION = "0.1.0"
