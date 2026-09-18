"""lib/tools/defs/web_defs.py — web fetch / search / browser / tail_log (6 个)."""

WEB_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch a URL and return its text content (HTML stripped). "
                "Primary tool for real-time info. "
                "For searching, prefer the web_search tool over fetching search engine pages directly. "
                "NEVER answer 'cannot get real-time info' -- always call this tool first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url":       {"type": "string"},
                    "max_chars": {"type": "integer", "description": "Max chars (default 6000)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Deep multi-source web research. For complex queries needing multiple sources.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":  {"type": "string"},
                    "effort": {"type": "string", "enum": ["low", "mid", "high"]},
                    "region": {"type": "string", "enum": ["domestic", "international"],
                               "description": "Search region: domestic(DuckDuckGo/Bing CN) or international(DuckDuckGo/Google/Bing EN)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": (
                "[v1.0] 真实浏览器搜索 — 突破反爬 + 拿到 JS 渲染后的真实结果 + 截图留证。"
                "何时用: web_search 返回'搜索无结果' / 结果全是无关/垃圾页 / 需要看到页面长啥样时。"
                "返回: 结构化 top-K + 截图路径 (PNG, 保存在 workspace/screenshots/)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":  {"type": "string", "description": "搜索关键词"},
                    "engine": {"type": "string", "enum": ["bing", "duckduckgo", "baidu", "sogou"],
                                "description": "搜索引擎 (默认 bing)"},
                    "top_k":  {"type": "integer", "description": "返回结果数 (默认 5, 最多 15)"},
                    "screenshot": {"type": "boolean", "description": "是否保存截图 (默认 True)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_login",
            "description": (
                "[P34-d-1] 真浏览器自动登录: goto → extract_form → 智能匹配 user/pwd selector → "
                "fill → click 登录按钮 → 检查跳转 + 截图. 适合需要登录后才能拿数据的场景 "
                "(SaaS / 内部后台 / LiteCode WebUI 自检). 只密码型 (LiteCode CHANGE_ME_PASSWORD) 和"
                "用户名+密码型 (大多数 SaaS) 都覆盖. 返回: {ok, msg, url, fields_filled, selectors, screenshot}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url":      {"type": "string", "description": "登录页 URL"},
                    "username": {"type": "string", "description": "用户名 (只密码型可不传)"},
                    "password": {"type": "string", "description": "密码"},
                    "user_hint": {"type": "string",
                                  "description": "可选: 用户名字段命中关键词 (如 'email' / 'phone')"},
                    "submit_selector": {"type": "string",
                                         "description": "可选: 登录按钮 selector (默认按 '登录'/'login' 文本找)"},
                    "timeout": {"type": "integer", "description": "总超时秒 (默认 30)"}
                },
                "required": ["url", "password"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read",
            "description": (
                "[v1.0] 真实浏览器打开 URL → 智能提取主正文 + 截图。"
                "比 web_fetch 强在: 能处理 JS 渲染 / 登录墙前置页 / 反爬, 能拿到用户实际看到的文字。"
                "何时用: web_fetch 返回 ERROR / 返回空白 / 页面明显是 JS SPA / 需要看到页面长啥样。"
                "返回: {title, text, screenshot_path}, text 是去噪后的主正文 (类似 readability)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url":       {"type": "string", "description": "目标 URL"},
                    "wait_for":  {"type": "string", "description": "可选: 等这个 CSS 选择器出现才算加载完"},
                    "max_chars": {"type": "integer", "description": "正文最大字符 (默认 8000)"},
                    "screenshot": {"type": "boolean", "description": "是否保存截图 (默认 True)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tail_log",
            "description": (
                "读文件尾部 N 行 (类似 tail -n). 用于看后台进程/日志/异步任务输出."
                "典型: 用户'看下 nginx/server.log 的最近输出' → tail_log(path='/var/log/nginx/access.log', lines=30). "
                "或: execute_shell(background=true) 起服务后, list_bg 拿到 log 路径, tail_log(path=<log>) 看启动是否成功. "
                "避免一次性 read_file 整个超大日志."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":  {"type": "string", "description": "日志文件路径 (绝对路径)"},
                    "lines": {"type": "integer", "description": "尾部行数, 默认 30, 最大 500"},
                    "follow_seconds": {"type": "integer", "description": "可选: 持续 follow N 秒 (类似 tail -f), 默认 0 = 立即返回"}
                },
                "required": ["path"]
            }
        }
    },
]
