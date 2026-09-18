"""git_defs.py — git 操作工具定义."""

GIT_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "查看 git 仓库状态 (已改/未追踪/分支). 等同 git status --short --branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "仓库路径, 默认当前目录", "default": "."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "查看 diff. staged=true 看已暂存的 diff, path 限定文件.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd":    {"type": "string", "default": "."},
                    "staged": {"type": "boolean", "description": "看 --cached diff", "default": False},
                    "path":   {"type": "string", "description": "限定文件路径 (可选)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "查看 git 提交历史 (--oneline --decorate).",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "default": "."},
                    "n":   {"type": "integer", "description": "最多显示 N 条, 默认 20", "default": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "暂存并提交. add_all=true 等于 git add -A; files 列表指定要 stage 的文件; message 必填.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd":     {"type": "string", "default": "."},
                    "message": {"type": "string", "description": "commit message"},
                    "add_all": {"type": "boolean", "description": "git add -A 先暂存所有改动", "default": False},
                    "files":   {"type": "array", "items": {"type": "string"}, "description": "指定要 stage 的文件列表"},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_checkout",
            "description": "切换或新建分支.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd":    {"type": "string", "default": "."},
                    "branch": {"type": "string", "description": "分支名"},
                    "create": {"type": "boolean", "description": "git checkout -b (新建分支)", "default": False},
                },
                "required": ["branch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_stash",
            "description": "stash 操作. action: push(保存)/pop(恢复)/list(查看)/drop(丢弃).",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd":    {"type": "string", "default": "."},
                    "action": {"type": "string", "enum": ["push", "pop", "list", "drop"], "default": "push"},
                },
            },
        },
    },
]
