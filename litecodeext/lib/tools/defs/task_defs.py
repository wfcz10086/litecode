"""task_defs.py — 会话内任务追踪工具定义."""

TASK_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "task_create",
            "description": "创建一个任务项 (会话内追踪). 类似 Claude Code TaskCreate. 返回 task id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "任务标题"},
                    "description": {"type": "string", "description": "详细描述 (可选)"},
                    "priority":    {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "列出当前会话所有任务及状态. status 过滤: pending/in_progress/completed/blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "按状态过滤 (可选)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_update",
            "description": "更新任务状态或追加备注.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id":     {"type": "string", "description": "task id (task_create 返回的 8 位 hex)"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]},
                    "note":   {"type": "string", "description": "追加备注"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_done",
            "description": "标记任务完成.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id":   {"type": "string", "description": "task id"},
                    "note": {"type": "string", "description": "完成备注"},
                },
                "required": ["id"],
            },
        },
    },
]
