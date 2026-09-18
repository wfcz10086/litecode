"""lib/tools/defs/shell_defs.py — shell / bg / desktop 操控工具 (8 个)."""

SHELL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "execute_shell",
            "description": (
                "Execute a bash shell command. Auto-infers timeout. "
                "Patches pip with --break-system-packages. Retries transient errors. "
                "For long-lived services (uvicorn/flask/node etc.) pass background=true. "
                "IMPORTANT: background 进程默认会在 session 结束时被自动清理。"
                "如果是用户明确要求的持久服务（不希望 session 结束后被杀），加 keep_alive=true。"
                "一次性测试服务（启动→curl→kill）的标准做法: 用 background=true 启动, "
                "测试完用 kill_bg 工具显式清理，避免残留进程占用端口。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command":    {"type": "string"},
                    "timeout":    {"type": "integer", "description": "Seconds, 0=unlimited. 非 background 模式强制 ≤900s"},
                    "background": {"type": "boolean", "description": "nohup mode for long-lived processes"},
                    "health_url": {"type": "string",  "description": "URL to poll when background=true"},
                    "keep_alive": {"type": "boolean", "description": "True=session 结束也保留此进程（用户明确要求长跑的服务）。默认 False=session 结束自动清理"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_bg",
            "description": (
                "列出本 session 启动的所有后台进程。"
                "用于在宣告任务完成前检查是否有残留进程需要清理。"
                "返回每个进程的 pid / 命令 / 启动时间 / 是否长期保留(keep)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kill_bg",
            "description": (
                "停止一个或多个后台进程（先 SIGTERM，1 秒无效则 SIGKILL）。"
                "适用场景: 临时测试完服务后清理; 发现端口占用; 停止出错的服务。"
                "调用 list_bg 获取 pid 列表。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pid":  {"type": "string", "description": "要 kill 的 pid；'all' 表示本 session 所有非 keep_alive 进程"},
                    "force": {"type": "boolean", "description": "True=连 keep_alive 的也杀"}
                },
                "required": ["pid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_exec",
            "description": (
                "在 noVNC 桌面 (DISPLAY=:99) 执行 shell 命令. 用户能在 noVNC 看到结果窗口."
                "何时用: 用户说'打开 chromium/打开网页/打开文件/在桌面跑 xxx'."
                "典型用例: "
                "  desktop_exec(cmd='chromium https://www.tradingview.com/symbols/ETHUSDT.P/?exchange=BINANCE') "
                "  desktop_exec(cmd='xdg-open /tmp/report.pdf') "
                "  desktop_exec(cmd='thunar /home/long')  # 文件管理器 "
                "bg=true (默认): 长进程, 立即返回; bg=false: 等结果."
                "⚠ binance.com 在中国 IP 被 AWS WAF 拦, 看合约/K线一律走 tradingview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "shell 命令, 自动带 DISPLAY=:99"},
                    "bg":  {"type": "boolean", "description": "后台运行 (默认 true, 浏览器这种长进程必须 true)"}
                },
                "required": ["cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_screenshot",
            "description": (
                "截当前 :99 桌面到 PNG. 让用户/你看到桌面当前状态."
                "何时用: 调 desktop_exec 后等几秒, 截图确认页面/窗口已打开."
                "返回 {ok, path, bytes}, path 可在聊天里直接引用 (前端会自动渲染图片)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "可选: 输出路径, 默认 /tmp/litecode_workspace/screenshots/desktop-<ts>.png"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_key",
            "description": (
                "向 :99 桌面发送按键组合 (xdotool). 用于浏览器/编辑器快捷键."
                "key 语法: 'ctrl+t' (新标签) / 'ctrl+l' (地址栏) / 'Return' / 'Escape' / 'F11' / 'alt+F4'."
                "典型: 先点焦点元素或 desktop_exec 起浏览器 → desktop_key('ctrl+l') → desktop_type(url) → desktop_key('Return')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "xdotool key 语法, 如 ctrl+t / Return / alt+F4"}
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_type",
            "description": (
                "向 :99 桌面键入文本 (xdotool type). 模拟用户键盘输入."
                "典型: 在地址栏键入 URL / 在搜索框键入关键词. 中文目前无输入法不可靠, 用英文/URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要键入的文本 (建议英文/URL/数字)"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_click",
            "description": (
                "[A 2026-05-22] 鼠标点击 (x,y) — xdotool mousemove + click."
                "用法: 先 desktop_screenshot 截图 → 你 (多模态模型) 看截图 → 估出按钮像素坐标 → desktop_click(x=X, y=Y)."
                "桌面分辨率 1280x720; (0,0)=左上, (1280,720)=右下."
                "⚠ Tradingview 时间周期切换不要用 click, 直接用 URL `?interval=W` 直跳更稳."
                "click 适合点'OK/确定/关闭/特定页面元素' 等无 URL 替代的按钮."
                "button: left/right/middle, 默认 left."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X 坐标 (0-1280)"},
                    "y": {"type": "integer", "description": "Y 坐标 (0-720)"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"],
                               "description": "鼠标按键, 默认 left"}
                },
                "required": ["x", "y"]
            }
        }
    },
]
