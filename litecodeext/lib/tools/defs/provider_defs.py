"""lib/tools/defs/provider_defs.py — 按需工具组 providers (「🔌 能力接入」, lib/providers.py): 6 个工具。

不进常驻前缀: 用户提到生图/生视频/配乐/通知/接入等, 或模型调 enable_tools(groups=["providers"]) 时才随会话加入。
"""

PROVIDER_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "list_providers",
            "description": ("列出用户在「🔌 能力接入」里配置的外部服务 (生图/生视频/语音合成/语音识别/搜索/音乐/向量/重排/通知/"
                            "对象存储/其他 HTTP): 名字、类型、协议、是否默认、一句话用途。要调用某个服务先 get_provider 看完整用法。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["image", "video", "tts", "asr", "search", "music", "embedding",
                                                        "rerank", "notify", "storage", "other"],
                             "description": "只看某一类, 省略=全部"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_provider",
            "description": ("取一张接入卡片的完整明文: 地址、key、模型、extra 和用法说明 (curl 示例)。没有内置适配的服务"
                            "照用法用 execute_shell 跑 curl 调用; 回复用户时不要复述完整 key。"),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "卡片名字或 id (list_providers 里的)"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": ("文生图: 按「能力接入」里默认的生图服务出图 (OpenAI 兼容接口 / ComfyUI 自动调用), 失败自动换下一个启用的; "
                            "图片存本会话产物目录, 回复里写出完整路径 (网页显示预览)。返回'请按用法自行调用'时照 get_provider 的用法 curl。"
                            "要改图/探索 ComfyUI 模型走 comfyui-image 技能。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "画面描述; 图里要出现的文字用双引号括起来"},
                    "width": {"type": "integer", "description": "宽, 默认 1024"},
                    "height": {"type": "integer", "description": "高, 默认 1024"},
                    "provider": {"type": "string", "description": "指定接入名, 省略=默认"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": ("生视频 (异步): ComfyUI 接入在后台出片, 立即返回日志路径, 之后用 tail_log 看进度, 日志里 [out] 行是 mp4 路径; "
                            "其它平台返回完整用法, 按用法提交任务并轮询。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "镜头描述"},
                    "image": {"type": "string", "description": "可选: 首帧图片路径 (图生视频)"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                    "frames": {"type": "integer", "description": "帧数 (ComfyUI H3: 17n+5, 73≈3 秒)"},
                    "provider": {"type": "string", "description": "指定接入名, 省略=默认"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_music",
            "description": ("生成配乐/音乐: 按「能力接入」里默认的音乐服务; 没配或失败时用本机 bgm-music 程序化生成无版权 BGM。"
                            "产物写进回复 (网页出播放器)。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "风格/情绪/用途描述"},
                    "mood": {"type": "string", "enum": ["calm", "upbeat", "epic", "sad", "tech", "warm", "funny", "suspense"],
                             "description": "本地 BGM 的情绪, 默认 upbeat"},
                    "duration": {"type": "integer", "description": "秒, 默认 30"},
                    "provider": {"type": "string", "description": "指定接入名, 省略=默认"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_notify",
            "description": "通过「能力接入」里的通知渠道 (企业微信/飞书/钉钉群机器人、SMTP 邮件) 发一条文本消息; 只在用户要求推送/通知时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "通知卡片名, 省略=默认通知渠道"},
                    "text": {"type": "string", "description": "消息正文"},
                    "title": {"type": "string", "description": "可选标题 (邮件主题)"},
                },
                "required": ["text"],
            },
        },
    },
]
