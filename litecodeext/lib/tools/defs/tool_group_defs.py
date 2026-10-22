"""lib/tools/defs/tool_group_defs.py — enable_tools: 按需加入低频工具组 (lib/tool_groups.py)。"""
from ...tool_groups import describe as _describe_groups

TOOL_GROUP_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "enable_tools",
            "description": (
                "为本会话加入低频工具组, 下一步起可直接调用, 之后整个会话一直可用。"
                "用户提到相关事物时系统通常已自动加入; 需要的工具不在列表里时再调用本工具 "
                "(也可直接调用组内工具名, 会自动加入)。可用组:\n" + _describe_groups()
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "groups": {"type": "array", "items": {"type": "string"},
                               "description": "组名 (见上), 也可写组内工具名"},
                },
                "required": ["groups"],
            },
        },
    },
]
