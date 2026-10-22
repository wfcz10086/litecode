"""lib/tools/defs/settings_defs.py — 按需工具组 voice / settings (2 个): text_to_speech / manage_models。

两者都在 lib/tool_groups.py 的按需组里, 不进常驻前缀; 用户提到朗读/配音或加模型/换模型时才随会话加入。
"""

SETTINGS_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "text_to_speech",
            "description": (
                "文字转语音 (朗读/配音/语音播报), 产出音频存本会话产物目录, 返回路径和时长; 回复里写出完整路径, 网页会显示播放器。"
                "默认先用本地纯 CPU 引擎 (不联网, 音色 女声1/女声2/女声3/男声1/男声2/男声3, 晓晓/云希等常用名会映射过去), "
                "失败或没装再用 edge-tts (免 key, 需联网: 晓晓/云希/云扬/晓伊/云健/粤语晓佳/台湾晓臻/Jenny/Guy); "
                "配了百炼 key 时还可用百炼音色 (芊悦/晨煦/北京-晓东/四川-晴儿/粤语-阿强…)。方言/粤语/英文音色自动走在线引擎。"
                "长文本自动按句切分合成再拼接, 单次上限 1 万字。不确定音色先 action=voices 列出。"
                "做带字幕/画面的视频走 video-studio 技能。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["speak", "voices"], "description": "speak(默认)=合成; voices=列可用音色"},
                    "text": {"type": "string", "description": "要朗读的文字"},
                    "voice": {"type": "string", "description": "音色, 默认晓晓"},
                    "rate": {"type": "string", "description": "语速, 如 '+20%' / '-10%' / '1.2' (倍速)"},
                    "pitch": {"type": "string", "description": "音调, 如 '+5Hz' / '-3Hz'"},
                    "volume": {"type": "string", "description": "音量, 如 '+10%'"},
                    "format": {"type": "string", "enum": ["mp3", "wav", "m4a"], "description": "默认 mp3"},
                    "engine": {"type": "string", "enum": ["auto", "local", "edge", "dashscope"], "description": "默认 auto (本地→edge→百炼, 失败自动换下一个)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_models",
            "description": (
                "管理大模型配置 (设置页「模型」列表)。list=列出 (key 只显示前4后4); presets=内置提供商预设 "
                "(DeepSeek/Kimi/智谱/百炼/硅基流动/火山方舟/MiniMax/OpenRouter/OpenAI); "
                "add=新增 (同 id 已存在要 overwrite=true 才覆盖): 先连通测试 (30 秒超时), 通过才写入, 同时探测能否看图与思考; 用户只给了 key 也行 "
                "(按 key 格式或 provider 补 base_url 和默认模型名); test=测试已配置的模型; "
                "set_default=切换默认模型, remove=删除模型 —— 这两个改真实配置, 只在用户明确要求时调用, 当前默认模型不能删。"
                "回复用户时不要复述完整 key。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "presets", "add", "test", "set_default", "remove"]},
                    "id": {"type": "string", "description": "模型条目 id: 用户给模型起的名字填这里 (add 时省略则用 model); test/set_default/remove 必填, 用 list 里的 id"},
                    "provider": {"type": "string", "description": "add: 预设名, 如 deepseek / kimi / glm / dashscope / siliconflow / ark / openrouter"},
                    "base_url": {"type": "string", "description": "add: OpenAI 兼容地址 (…/v1); 给了 provider 可省略"},
                    "model": {"type": "string", "description": "add: 发给接口的模型名; 省略用预设默认"},
                    "api_key": {"type": "string", "description": "add: API key"},
                    "label": {"type": "string", "description": "add: 设置页显示名, 可选 (默认 '<id> · 平台')"},
                    "overwrite": {"type": "boolean", "description": "add: id 已存在时覆盖它, 默认 false (拒绝, 不改动)"},
                    "context_window": {"type": "integer", "description": "add: 上下文长度, 可选"},
                },
                "required": ["action"],
            },
        },
    },
]
