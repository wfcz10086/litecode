"""框架级快照工具定义 (snapshot_list / snapshot_restore)。

区别于 git_defs: git_* 是 agent 操作项目自己的 git; snapshot_* 操作框架在
每个 task 边界自动打的旁路快照, 用于回到某个绿点。
"""
SNAPSHOT_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "snapshot_list",
            "description": (
                "列出某项目的框架级自动快照 (最新在前)。框架在每个 task 边界会自动"
                "给你改动的项目打一个可回滚快照。当你把项目改坏、想看有哪些可回退的"
                "绿点时用它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string",
                                "description": "项目根的绝对路径, 如 /tmp/yijing-go-v2"},
                    "limit": {"type": "integer", "description": "最多列几条, 默认 20"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot_restore",
            "description": (
                "把项目回滚到某个自动快照。回滚前会自动保存当前状态, 时间线只增不减, "
                "所以任何回滚本身也能再回滚。当你确认某步改崩了、想回到之前的绿点时用它 —— "
                "比手工 apply_blocks 往回改可靠。先用 snapshot_list 拿到目标 hash。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "项目根绝对路径"},
                    "hash": {"type": "string", "description": "目标快照的短 hash (来自 snapshot_list)"},
                },
                "required": ["project", "hash"],
            },
        },
    },
]
