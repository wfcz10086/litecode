"""lib/provider_presets.py — 「能力接入」新增卡片时的预设模板。

只写能从官方文档确认的地址和参数 (2026-10-23 核对, doc 字段是出处); 确认不了的只给 base_url 占位 +
"请按官方文档填写"。usage 里的 {api_key} {base_url} {model} {prompt} {query} {text} {task_id} 等是占位,
agent 读卡片时 {api_key}/{base_url}/{model} 会自动换成卡片里的值, 其余由它按任务填。
"""
from __future__ import annotations

_J = "-H 'Content-Type: application/json'"
_B = "-H 'Authorization: Bearer {api_key}'"


def _p(key, label, kind, protocol, base_url="", model="", usage="", note="", doc="", verified=True, extra=None):
    return {"key": key, "label": label, "kind": kind, "protocol": protocol, "base_url": base_url, "model": model,
            "usage": usage.strip("\n"), "note": note, "doc": doc, "verified": verified, "extra": extra or {}}


TODO_USAGE = "请按官方文档填写: 接口路径、请求体、结果字段。写成 curl 示例 (可用 {api_key} {prompt} 占位), 测试按钮会跑第一条 curl。"

PRESETS: list[dict] = [
    # ── 生图 ──
    _p("openai_images", "OpenAI 兼容图片接口", "image", "openai_images", "https://api.openai.com/v1", "",
       f"""curl -s {{base_url}}/images/generations {_B} {_J} \\
  -d '{{"model": "{{model}}", "prompt": "{{prompt}}", "size": "1024x1024"}}'
# 结果: data[0].b64_json (GPT image 系列只回 base64) 或 data[0].url; generate_image 工具自动处理两种""",
       "OpenAI 及兼容 /v1/images/generations 的服务; model 按平台填 (如 gpt-image-1)",
       "https://developers.openai.com/api/docs/guides/image-generation"),
    _p("comfyui_qwen", "ComfyUI · Qwen-Image 2.1", "image", "comfyui", "127.0.0.1:8188", "",
       """C="python3 ${LITECODE_HOME:-/opt/litecode}/skills/comfyui-image/scripts/comfy.py"
$C probe {base_url}                       # 先探: 显卡、模型、可用模板
$C run {base_url} -w qwen21_t2i --prompt '{prompt}' --width 1024 --height 1024
# 内网地址加 --via-ssh <跳板>; 改图 -w qwen21_edit --input image=本地图; 详见 comfyui-image 技能""",
       "自建 ComfyUI, generate_image 委托 comfy.py; extra.workflow 可换模板/saved:工作流, extra.via_ssh 填跳板",
       "skills/comfyui-image/SKILL.md", extra={"workflow": "qwen21_t2i"}),
    _p("siliconflow_image", "硅基流动 生图", "image", "openai_images", "https://api.siliconflow.cn/v1",
       "Qwen/Qwen-Image",
       f"""curl -s {{base_url}}/images/generations {_B} {_J} \\
  -d '{{"model": "{{model}}", "prompt": "{{prompt}}", "image_size": "1024x1024"}}'
# 结果: images[0].url (1 小时内有效, 及时下载); 尺寸字段是 image_size 不是 size""",
       "模型还有 Tongyi-MAI/Z-Image-Turbo、black-forest-labs/FLUX.1-schnell 等",
       "https://docs.siliconflow.com/cn/api-reference/images/images-generations", extra={"size_field": "image_size"}),
    _p("ark_seedream", "火山方舟 · 即梦 Seedream 生图", "image", "openai_images",
       "https://ark.cn-beijing.volces.com/api/v3", "doubao-seedream-5-0-pro-260628",
       f"""curl -s {{base_url}}/images/generations {_B} {_J} \\
  -d '{{"model": "{{model}}", "prompt": "{{prompt}}", "size": "2K", "response_format": "url", "watermark": false}}'
# 结果: data[0].url; size 用 1K/1.5K/2K (5.0 pro) 或总像素 ≥ 921600 的 "宽x高", 不支持 1024x1024 这类小图""",
       "api_key 填方舟 API Key; model 填控制台开通的 Seedream 版本",
       "https://docs.volcengine.com/docs/82379/1541523",
       extra={"size": "2K", "body": {"response_format": "url", "watermark": False}}),
    _p("dashscope_wan_image", "阿里云百炼 · 通义万相 生图", "image", "http", "https://dashscope.aliyuncs.com",
       "wan2.2-t2i-flash",
       f"""curl -s -X POST {{base_url}}/api/v1/services/aigc/text2image/image-synthesis {_B} {_J} \\
  -H 'X-DashScope-Async: enable' \\
  -d '{{"model": "{{model}}", "input": {{"prompt": "{{prompt}}"}}, "parameters": {{"size": "1024*1024", "n": 1}}}}'
# 返回 output.task_id (异步), 每隔几秒查一次, output.task_status=SUCCEEDED 后图片在 output.results[0].url (24 小时有效):
curl -s {{base_url}}/api/v1/tasks/{{task_id}} {_B}""",
       "异步任务: 先提交再轮询; 模型还有 wan2.6-t2i / wanx2.1-t2i-turbo 等",
       "https://help.aliyun.com/zh/model-studio/text-to-image-v2-api-reference"),
    # ── 生视频 ──
    _p("comfyui_h3", "ComfyUI · MiniMax H3 视频", "video", "comfyui", "127.0.0.1:8188", "",
       """C="python3 ${LITECODE_HOME:-/opt/litecode}/skills/comfyui-image/scripts/comfy.py"
$C probe {base_url}
$C run {base_url} -w h3_t2v --prompt '{prompt}' --frames 73          # 文生视频
$C run {base_url} -w h3_i2v --input first=首帧.png --prompt '{prompt}'  # 图生视频
# 宽高 32 的倍数, 帧数 17n+5; 线上机先等队列空; 详见 comfyui-video 技能""",
       "generate_video 后台跑 comfy.py, 立即返回日志路径", "skills/comfyui-video/SKILL.md"),
    _p("ark_seedance", "火山方舟 · Seedance 视频", "video", "http", "https://ark.cn-beijing.volces.com/api/v3",
       "doubao-seedance-2-5-260628",
       f"""curl -s -X POST {{base_url}}/contents/generations/tasks {_B} {_J} \\
  -d '{{"model": "{{model}}", "content": [{{"type": "text", "text": "{{prompt}}"}}], "resolution": "720p", "ratio": "16:9", "duration": 5}}'
# 返回 id; 查询 status (queued/running/succeeded/failed), 成功后视频在 content.video_url:
curl -s {{base_url}}/contents/generations/tasks/{{task_id}} {_B}
# 图生视频: content 里再加 {{"type": "image_url", "image_url": {{"url": "..."}}, "role": "first_frame"}}""",
       "异步任务", "https://docs.volcengine.com/docs/82379/1520757"),
    _p("dashscope_wan_video", "阿里云百炼 · 通义万相 视频", "video", "http", "https://dashscope.aliyuncs.com",
       "wan2.7-t2v",
       f"""curl -s -X POST {{base_url}}/api/v1/services/aigc/video-generation/video-synthesis {_B} {_J} \\
  -H 'X-DashScope-Async: enable' \\
  -d '{{"model": "{{model}}", "input": {{"prompt": "{{prompt}}"}}, "parameters": {{"resolution": "720P", "ratio": "16:9", "duration": 5}}}}'
# 返回 output.task_id; 轮询直到 output.task_status=SUCCEEDED, 视频在 output.video_url:
curl -s {{base_url}}/api/v1/tasks/{{task_id}} {_B}""",
       "异步任务 (wan2.7 用 resolution+ratio; 旧版模型参数以文档为准)",
       "https://help.aliyun.com/zh/model-studio/text-to-video-api-reference"),
    _p("minimax_hailuo", "MiniMax · 海螺视频", "video", "http", "https://api.minimax.cn", "MiniMax-Hailuo-2.3",
       f"""curl -s -X POST {{base_url}}/v1/video_generation {_B} {_J} \\
  -d '{{"model": "{{model}}", "prompt": "{{prompt}}", "duration": 6, "resolution": "768P"}}'
# 返回 task_id; 查询 status (Preparing/Queueing/Processing/Success/Fail), 成功给 file_id:
curl -s '{{base_url}}/v1/query/video_generation?task_id={{task_id}}' {_B}
# 用 file_id 换下载地址 (file.download_url):
curl -s '{{base_url}}/v1/files/retrieve?file_id={{file_id}}' {_B}""",
       "异步任务; 也可用 MiniMax-Hailuo-02", "https://platform.minimax.cn/docs/api-reference/video-generation-t2v"),
    _p("kling", "可灵 Kling", "video", "http", "https://api-beijing.klingai.com", "", TODO_USAGE,
       "鉴权: Authorization: Bearer <API Key> (或 AK/SK 生成 JWT); 文生视频的接口路径与参数请按官方文档填写",
       "https://kling.ai/document-api/api/get-started/authentication.md", verified=False),
    # ── 语音合成 ──
    _p("openai_tts", "OpenAI 兼容语音合成", "tts", "openai_tts", "https://api.openai.com/v1", "gpt-4o-mini-tts",
       f"""curl -s {{base_url}}/audio/speech {_B} {_J} \\
  -d '{{"model": "{{model}}", "input": "{{text}}", "voice": "coral", "response_format": "mp3"}}' -o out.mp3
# 响应直接是音频二进制""",
       "", "https://developers.openai.com/api/docs/guides/text-to-speech"),
    _p("dashscope_cosyvoice", "阿里云百炼 · CosyVoice 语音", "tts", "http", "https://dashscope.aliyuncs.com",
       "cosyvoice-v3-flash",
       f"""curl -s -X POST {{base_url}}/api/v1/services/audio/tts/SpeechSynthesizer {_B} {_J} \\
  -d '{{"model": "{{model}}", "input": {{"text": "{{text}}", "voice": "longanyang", "format": "mp3", "sample_rate": 24000}}}}'
# 结果: output.audio.url (下载即可); 仅北京地域""",
       "内置 text_to_speech 也能用百炼音色 (qwen3-tts-flash)", "https://help.aliyun.com/zh/model-studio/cosyvoice-tts-http-api"),
    _p("minimax_tts", "MiniMax · 语音合成", "tts", "http", "https://api.minimax.cn", "speech-2.8-hd",
       f"""curl -s -X POST {{base_url}}/v1/t2a_v2 {_B} {_J} \\
  -d '{{"model": "{{model}}", "text": "{{text}}", "stream": false, "voice_setting": {{"voice_id": "male-qn-qingse", "speed": 1, "vol": 1, "pitch": 0}}, "audio_setting": {{"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1}}, "output_format": "hex"}}'
# 结果: data.audio 是 hex 编码的音频 (bytes.fromhex 后写 mp3); output_format 改 url 则返回链接""",
       "", "https://platform.minimax.cn/docs/api-reference/speech-t2a-http"),
    # ── 语音识别 ──
    _p("openai_asr", "OpenAI 兼容语音识别", "asr", "openai_asr", "https://api.openai.com/v1", "gpt-4o-mini-transcribe",
       f"""curl -s {{base_url}}/audio/transcriptions {_B} \\
  -F model={{model}} -F file=@录音.mp3
# 结果: text; 自建 whisper 兼容服务把 base_url 换成自己的地址""",
       "", "https://developers.openai.com/api/docs/guides/speech-to-text"),
    # ── 搜索 ──
    _p("searxng", "SearXNG", "search", "searxng", "http://127.0.0.1:18888", "",
       """curl -s '{base_url}/search?q={query}&format=json'
# 结果: results[].title / url / content; 需在 settings.yml 的 search.formats 里开启 json""",
       "开源元搜索, 本机自建免 key", "https://docs.searxng.org/dev/search_api.html"),
    _p("bocha", "博查搜索", "search", "http", "https://api.bochaai.com", "",
       f"""curl -s -X POST {{base_url}}/v1/web-search {_B} {_J} \\
  -d '{{"query": "{{query}}", "freshness": "noLimit", "summary": true, "count": 8}}'
# 结果: webPages.value[] (name/url/snippet/summary); 有的返回外面还包一层 data, 两种都看""",
       "国内可用的 AI 搜索 API", "https://open.bochaai.com/"),
    _p("tavily", "Tavily", "search", "http", "https://api.tavily.com", "",
       f"""curl -s -X POST {{base_url}}/search {_B} {_J} \\
  -d '{{"query": "{{query}}"}}'
# 结果: results[].title / url / content / score""",
       "海外 AI 搜索 API, key 以 tvly- 开头", "https://docs.tavily.com/documentation/api-reference/endpoint/search"),
    # ── 音乐 ──
    _p("minimax_music", "MiniMax · 音乐", "music", "http", "https://api.minimax.cn", "music-3.0",
       f"""curl -s -X POST {{base_url}}/v1/music_generation {_B} {_J} \\
  -d '{{"model": "{{model}}", "prompt": "{{prompt}}", "is_instrumental": true, "output_format": "url"}}'
# 带歌词: 去掉 is_instrumental, 加 "lyrics": "歌词\\n..."; 结果在 data.audio (默认 hex, output_format=url 时是链接)""",
       "纯音乐 BGM / 带词歌曲; 也有 music-2.6", "https://platform.minimax.cn/docs/api-reference/music-generation"),
    _p("suno", "Suno 类音乐服务", "music", "http", "", "", TODO_USAGE,
       "Suno 目前没有官方公开 API (仅合作伙伴计划); 第三方代理接口请按其文档填写", "", verified=False),
    # ── 向量 / 重排 ──
    _p("openai_embeddings", "OpenAI 兼容向量", "embedding", "openai_embeddings", "https://api.siliconflow.cn/v1",
       "Qwen/Qwen3-Embedding-8B",
       f"""curl -s {{base_url}}/embeddings {_B} {_J} \\
  -d '{{"model": "{{model}}", "input": "{{text}}"}}'
# 结果: data[0].embedding; 自建 vLLM/Ollama 兼容服务换 base_url 与 model 即可""",
       "默认填的是硅基流动; 只做卡片和测试, 不改动现有知识库",
       "https://docs.siliconflow.com/cn/api-reference"),
    _p("siliconflow_rerank", "重排 (/rerank)", "rerank", "openai_rerank", "https://api.siliconflow.cn/v1",
       "Qwen/Qwen3-Reranker-8B",
       f"""curl -s {{base_url}}/rerank {_B} {_J} \\
  -d '{{"model": "{{model}}", "query": "{{query}}", "documents": ["文档1", "文档2"], "top_n": 5}}'
# 结果: results[].index / relevance_score""",
       "默认填的是硅基流动; vLLM 等自建服务也提供 /rerank", "https://docs.siliconflow.com/cn/api-reference"),
    # ── 通知 ──
    _p("wecom_bot", "企业微信群机器人", "notify", "wecom_webhook",
       "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=粘贴机器人key", "",
       """curl -s '{base_url}' -H 'Content-Type: application/json' \\
  -d '{"msgtype": "text", "text": {"content": "{text}"}}'
# 成功返回 errcode=0; 每个机器人 20 条/分钟""",
       "群设置 → 添加群机器人 → 复制 webhook 地址", "https://developer.work.weixin.qq.com/document/path/91770"),
    _p("feishu_bot", "飞书自定义机器人", "notify", "feishu_webhook",
       "https://open.feishu.cn/open-apis/bot/v2/hook/粘贴token", "",
       """curl -s -X POST '{base_url}' -H 'Content-Type: application/json' \\
  -d '{"msg_type": "text", "content": {"text": "{text}"}}'
# 开了签名校验: extra.secret 填密钥, send_notify 自动带 timestamp+sign""",
       "", "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot"),
    _p("dingtalk_bot", "钉钉自定义机器人", "notify", "dingtalk_webhook",
       "https://oapi.dingtalk.com/robot/send?access_token=粘贴token", "",
       """curl -s '{base_url}' -H 'Content-Type: application/json' \\
  -d '{"msgtype": "text", "text": {"content": "{text}"}}'
# 安全设置选"加签": extra.secret 填 SEC 开头的密钥, send_notify 自动带 timestamp+sign""",
       "加签算法已核对官方文档; 文本消息体按常用格式 (官方页未能逐字核对)", "", verified=False),
    _p("smtp", "SMTP 邮件", "notify", "smtp", "smtp.qq.com:465", "",
       """SMTP 服务器 {base_url}, 账号 {username}, 密码/授权码见 api_key, 收件人 {to}
send_notify 自动发信; 465 端口走 SSL, 其余端口自动 STARTTLS""",
       "api_key 填邮箱授权码; extra 填 username / from / to (多个逗号分隔)", "",
       extra={"username": "", "from": "", "to": ""}),
    # ── 对象存储 ──
    _p("minio", "MinIO / S3 兼容存储", "storage", "s3", "http://127.0.0.1:9000", "",
       """S3 兼容: endpoint={base_url} region={region} AccessKeyId={access_key_id} Secret=api_key
python3 -c "import boto3; s3=boto3.client('s3', endpoint_url='{base_url}', aws_access_key_id='{access_key_id}', aws_secret_access_key='{api_key}', region_name='{region}'); print(s3.list_buckets())"
# 测试按钮 = 列桶 (SigV4 签名)""",
       "api_key 填 Secret Access Key; extra.access_key_id 填 Access Key", "",
       extra={"access_key_id": "", "region": "us-east-1"}),
    _p("aliyun_oss", "阿里云 OSS (S3 兼容)", "storage", "s3", "", "",
       "endpoint 请按阿里云 OSS 官方文档填写所在地域的 S3 兼容地址; 测试按钮 = 列桶。",
       "endpoint 与地域请按官方文档填写", "", verified=False, extra={"access_key_id": "", "region": ""}),
    _p("tencent_cos", "腾讯云 COS (S3 兼容)", "storage", "s3", "", "",
       "endpoint 请按腾讯云 COS 官方文档填写所在地域的 S3 兼容地址; 测试按钮 = 列桶。",
       "endpoint 与地域请按官方文档填写", "", verified=False, extra={"access_key_id": "", "region": ""}),
    # ── 自定义 ──
    _p("custom_http", "自定义 HTTP (curl)", "other", "http", "", "",
       f"""# 把服务的用法原样写在这里 (地址、鉴权、请求体、结果字段), agent 读了就能调用; 测试按钮跑第一条 curl
curl -s -X POST {{base_url}}/your/path {_B} {_J} \\
  -d '{{"prompt": "{{prompt}}"}}'""",
       "任意服务: 一段话写清楚怎么调", "", verified=True),
]
