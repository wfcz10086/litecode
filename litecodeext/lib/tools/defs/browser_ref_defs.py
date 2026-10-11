"""browser_ref_defs.py — 编号式浏览器操作工具定义 (实现见 core/tools/handlers/browser_ref.py)。"""

BROWSER_REF_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": (
                "打开网页 (可选) 并列出页面上所有可交互元素, 每个带编号, 如 `[3] button \"登录\"`、"
                "`[5] textbox \"搜索\" 值=\"\"`, 外加页面文字节选。需要在网页上点击/填写/选择时先调它, "
                "再用 browser_act 按编号操作, 不必写脚本或猜 CSS 选择器。同一会话内页面状态保留。"
                "只想读文章正文用 browser_read 更省。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "可选: 先打开这个 URL; 不填则对当前页面重新快照"},
                    "screenshot": {"type": "boolean", "description": "可选: 同时保存截图并返回路径 (默认 false)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_act",
            "description": (
                "按 browser_snapshot 给的编号操作页面, 操作后自动返回新快照 (编号会重排, 以新快照为准)。"
                "action: click / fill(value=文字) / select(value=选项文字) / hover / "
                "press(value=按键如 Enter) / goto(value=URL) / back / scroll(value=像素) / "
                "close(用完务必关闭释放浏览器)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["click", "fill", "select", "hover", "press", "goto", "back", "scroll", "close"]},
                    "ref": {"type": "integer", "description": "元素编号 (click/fill/select/hover 必填)"},
                    "value": {"type": "string", "description": "fill 的文字 / select 的选项 / press 的按键 / goto 的 URL"},
                    "screenshot": {"type": "boolean", "description": "可选: 操作后保存截图 (默认 false)"},
                },
                "required": ["action"],
            },
        },
    },
]
