"""lib/provider_presets.py — 「能力接入」新增卡片时的预设。

一张卡 = 标题 + 一段明文。预设只做两件事:
  how    一两句大白话 "去哪拿 / 要准备什么" (显示在输入框上面)
  usage  预填进明文框的示例: 一两句说明 + 「Key: / 地址:」行 + 可直接运行的 curl (每条一行 # 注释说明用途)
用户把「粘贴…」换成自己的值即可; 其余 (kind/protocol/base_url/api_key/model) 由 lib/provider_infer.py 从明文识别。

占位: {api_key} {base_url} 会自动换成卡片里识别出的 Key / 地址; {prompt} {text} {query} {task_id} {file_id}
{image_url} {image_url2} {image_b64} {first_frame} {last_frame} {ref_audio} {audio_file} 由 LiteCode 调用时填:
参考图没有链接时, LiteCode 会把本地图片转成 data:image/png;base64,… 或先上传 (ComfyUI 走 /upload/image)。
「# 测试用」注释下面那条 curl 是「测试」按钮跑的 (只验证 Key, 不提交付费任务); 没有标记就跑第一条。

只写能从官方文档确认的接口路径和字段 (2026-10-23 核对, doc 字段是出处); 确认不了的写「以官方文档为准」, 不编造。
"""
from __future__ import annotations


def _p(key, label, kind, protocol, how, usage, doc="", verified=True, extra=None, base_url="", model=""):
    return {"key": key, "label": label, "kind": kind, "protocol": protocol, "how": how.strip(),
            "usage": usage.strip("\n"), "doc": doc, "verified": verified, "extra": extra or {},
            "base_url": base_url, "model": model, "note": ""}


_IMG_NOTE = "# 参考图: {image_url} 填图片链接; 本地图片没有链接时, LiteCode 会自动转成 {image_b64} (data:image/png;base64,…)"
_AUTH = "-H 'Authorization: Bearer {api_key}'"
_JSON = "-H 'Content-Type: application/json'"

PRESETS: list[dict] = [
    # ══ 生图 ══
    _p("siliconflow_image", "硅基流动 · 生图", "image", "openai_images",
       "打开 cloud.siliconflow.cn → 「API 密钥」→「新建 API 密钥」, 复制后贴到下面 Key 那一行。按量收费。",
       f"""硅基流动生图 (按量收费)。
Key: 粘贴你的 Key
# 文生图: 结果在 images[0].url (1 小时内有效, 及时下载)
curl -s https://api.siliconflow.cn/v1/images/generations {_AUTH} {_JSON} \\
  -d '{{"model": "Qwen/Qwen-Image", "prompt": "{{prompt}}", "image_size": "1024x1024"}}'
# 改图 / 参考图: 用编辑模型 (如 Qwen/Qwen-Image-Edit), image 要传 base64 (data:image/png;base64,…)
curl -s https://api.siliconflow.cn/v1/images/generations {_AUTH} {_JSON} \\
  -d '{{"model": "Qwen/Qwen-Image-Edit", "prompt": "{{prompt}}", "image": "{{image_b64}}"}}'
# 多张参考图的字段名以官方文档为准""",
       "https://docs.siliconflow.com/cn/api-reference/images/images-generations", extra={"size_field": "image_size"}),
    _p("ark_seedream", "火山方舟 · 即梦 Seedream 生图", "image", "openai_images",
       "打开火山方舟控制台的「API Key 管理」创建 Key (console.volcengine.com/ark), 并在控制台开通要用的 Seedream 模型。按量收费。",
       f"""火山方舟 Seedream 生图 (按量收费)。
Key: 粘贴你的 Key
# 文生图: 结果在 data[0].url (24 小时内有效); size 用 1K / 2K 或 "宽x高"
curl -s https://ark.cn-beijing.volces.com/api/v3/images/generations {_AUTH} {_JSON} \\
  -d '{{"model": "doubao-seedream-5-0-pro-260628", "prompt": "{{prompt}}", "size": "2K", "response_format": "url", "watermark": false}}'
# 参考图 / 改图: image 填一张图 (链接或 data:image/png;base64,…)
curl -s https://ark.cn-beijing.volces.com/api/v3/images/generations {_AUTH} {_JSON} \\
  -d '{{"model": "doubao-seedream-5-0-pro-260628", "prompt": "{{prompt}}", "image": "{{image_url}}", "size": "2K", "response_format": "url", "watermark": false}}'
# 多张参考图: image 写成数组 (5.0 pro 最多 10 张)
curl -s https://ark.cn-beijing.volces.com/api/v3/images/generations {_AUTH} {_JSON} \\
  -d '{{"model": "doubao-seedream-5-0-pro-260628", "prompt": "{{prompt}}", "image": ["{{image_url}}", "{{image_url2}}"], "size": "2K", "response_format": "url", "watermark": false}}'
{_IMG_NOTE}""",
       "https://docs.volcengine.com/docs/82379/1541523",
       extra={"size": "2K", "body": {"response_format": "url", "watermark": False}}),
    _p("dashscope_wan_image", "阿里云百炼 · 通义万相 生图", "image", "http",
       "打开阿里云百炼控制台的「API Key」页面 (bailian.console.aliyun.com), 点「创建 API Key」后复制。按量收费。",
       f"""阿里云百炼 通义万相 生图 (按量收费, 异步: 先提交再查结果)。
Key: 粘贴你的 Key
# ① 文生图: 提交任务, 返回 output.task_id
curl -s -X POST https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis {_AUTH} {_JSON} \\
  -H 'X-DashScope-Async: enable' \\
  -d '{{"model": "wan2.2-t2i-flash", "input": {{"prompt": "{{prompt}}"}}, "parameters": {{"size": "1024*1024", "n": 1}}}}'
# ② 查结果: 每隔几秒查一次, output.task_status 为 SUCCEEDED 就完成, 图片在 output.results[0].url (24 小时有效); FAILED 表示失败
curl -s https://dashscope.aliyuncs.com/api/v1/tasks/{{task_id}} {_AUTH}
# 参考图 / 改图 (万相图像编辑 2.5, images 最多 3 张; 同样按 ② 查结果, 接口地址以官方文档为准):
curl -s -X POST https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis {_AUTH} {_JSON} \\
  -H 'X-DashScope-Async: enable' \\
  -d '{{"model": "wan2.5-i2i-preview", "input": {{"prompt": "{{prompt}}", "images": ["{{image_url}}"]}}}}'
{_IMG_NOTE}
# 测试用 (只验证 Key, 不提交任务): 查一个不存在的任务
curl -s https://dashscope.aliyuncs.com/api/v1/tasks/litecode-test {_AUTH}""",
       "https://help.aliyun.com/zh/model-studio/text-to-image-v2-api-reference"),
    _p("openai_images", "OpenAI 兼容 · 生图", "image", "openai_images",
       "任何兼容 OpenAI 接口的服务都行: 填它的地址 (通常以 /v1 结尾) 和 Key。OpenAI 官方的 Key 在 platform.openai.com 的 API keys 页面创建。",
       f"""OpenAI 兼容生图接口。
地址: https://api.openai.com/v1
Key: 粘贴你的 Key
# 文生图: 结果在 data[0].b64_json 或 data[0].url
curl -s {{base_url}}/images/generations {_AUTH} {_JSON} \\
  -d '{{"model": "gpt-image-1", "prompt": "{{prompt}}", "size": "1024x1024"}}'
# 改图 / 参考图: 用 /images/edits 上传图片 (可传多张 image[]), 其它平台是否支持以其文档为准
curl -s {{base_url}}/images/edits {_AUTH} -F model=gpt-image-1 -F 'image[]=@{{image_file}}' -F 'prompt={{prompt}}'""",
       "https://developers.openai.com/api/docs/guides/image-generation"),
    _p("comfyui_qwen", "ComfyUI · Qwen-Image 2.1 (自建)", "image", "comfyui",
       "ComfyUI 是开源免费的出图软件, 装在有显卡的电脑上; 启动时加 --listen 参数让别的机器能访问 (默认端口 8188)。把地址填到下面, 如 10.0.0.5:8188。",
       """自建 ComfyUI 生图 (开源免费, 用自己的显卡)。
地址: 127.0.0.1:8188
# 测试: 看 ComfyUI 版本和显卡
curl -s {base_url}/system_stats
# 出图用 comfyui-image 技能的脚本; 参考图写本地文件或链接, 本地文件会自动 POST /upload/image 上传
C="python3 ${LITECODE_HOME:-/opt/litecode}/skills/comfyui-image/scripts/comfy.py"
$C run {base_url} -w qwen21_t2i --prompt '{prompt}' --width 1024 --height 1024     # 文生图
$C run {base_url} -w qwen21_edit --input image={image_url} --prompt '{prompt}'     # 改图 (一张参考图)
$C run {base_url} -w qwen21_multi --input image={image_url} --input image2={image_url2} --prompt '{prompt}'   # 两图融合
# 内网地址要跳板时加 --via-ssh <跳板>; 详见 comfyui-image 技能""",
       "skills/comfyui-image/SKILL.md", extra={"workflow": "qwen21_t2i"}),
    # ══ 生视频 ══
    _p("ark_seedance", "火山方舟 · Seedance 生视频", "video", "http",
       "和即梦生图同一个火山方舟 Key (console.volcengine.com/ark 的「API Key 管理」), 并在控制台开通 Seedance 模型。按量收费。",
       f"""火山方舟 Seedance 生视频 (按量收费, 异步: 先提交再查结果)。
Key: 粘贴你的 Key
# ① 文生视频: 提交任务, 返回 id
curl -s -X POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks {_AUTH} {_JSON} \\
  -d '{{"model": "doubao-seedance-2-5-260628", "content": [{{"type": "text", "text": "{{prompt}}"}}], "resolution": "720p", "ratio": "16:9", "duration": 5}}'
# ② 查结果: 每隔 10 秒查一次, status 为 succeeded 就完成, 视频地址在 content.video_url; failed / expired 表示失败
curl -s https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{{task_id}} {_AUTH}
# 图生视频 (图片作首帧): 同样先 ① 提交再 ② 查
curl -s -X POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks {_AUTH} {_JSON} \\
  -d '{{"model": "doubao-seedance-2-5-260628", "content": [{{"type": "text", "text": "{{prompt}}"}}, {{"type": "image_url", "image_url": {{"url": "{{first_frame}}"}}, "role": "first_frame"}}], "resolution": "720p", "duration": 5}}'
# 首尾帧: 再加一张 role 为 last_frame 的图
curl -s -X POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks {_AUTH} {_JSON} \\
  -d '{{"model": "doubao-seedance-2-5-260628", "content": [{{"type": "text", "text": "{{prompt}}"}}, {{"type": "image_url", "image_url": {{"url": "{{first_frame}}"}}, "role": "first_frame"}}, {{"type": "image_url", "image_url": {{"url": "{{last_frame}}"}}, "role": "last_frame"}}], "resolution": "720p", "duration": 5}}'
# 参考生视频: 图片 role 用 reference_image (哪些模型、几张图能组合以官方文档为准)
curl -s -X POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks {_AUTH} {_JSON} \\
  -d '{{"model": "doubao-seedance-2-5-260628", "content": [{{"type": "text", "text": "{{prompt}}"}}, {{"type": "image_url", "image_url": {{"url": "{{image_url}}"}}, "role": "reference_image"}}], "resolution": "720p", "duration": 5}}'
# 图片可以是链接, 也可以是 data:image/png;base64,…; 本地图片 LiteCode 会自动转 base64
# 测试用 (只验证 Key, 不提交任务): 查一个不存在的任务
curl -s https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/litecode-test {_AUTH}""",
       "https://docs.volcengine.com/docs/82379/1520757"),
    _p("dashscope_wan_video", "阿里云百炼 · 通义万相 生视频", "video", "http",
       "和万相生图同一个百炼 Key (bailian.console.aliyun.com 的「API Key」页面)。按量收费。",
       f"""阿里云百炼 通义万相 生视频 (按量收费, 异步: 先提交再查结果)。
Key: 粘贴你的 Key
# ① 文生视频: 提交任务, 返回 output.task_id
curl -s -X POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis {_AUTH} {_JSON} \\
  -H 'X-DashScope-Async: enable' \\
  -d '{{"model": "wan2.7-t2v", "input": {{"prompt": "{{prompt}}"}}, "parameters": {{"resolution": "720P", "ratio": "16:9", "duration": 5}}}}'
# ② 查结果: 每隔 10 秒查一次, output.task_status 为 SUCCEEDED 就完成, 视频在 output.video_url (24 小时有效); FAILED 表示失败
curl -s https://dashscope.aliyuncs.com/api/v1/tasks/{{task_id}} {_AUTH}
# 图生视频 (图片作首帧, 字段 img_url): 同样先提交再按 ② 查; 其它参数以官方文档为准
curl -s -X POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis {_AUTH} {_JSON} \\
  -H 'X-DashScope-Async: enable' \\
  -d '{{"model": "wan2.6-i2v-flash", "input": {{"prompt": "{{prompt}}", "img_url": "{{first_frame}}"}}}}'
# 首尾帧 (字段 first_frame_url / last_frame_url, 注意接口路径不同): 参数以官方文档为准
curl -s -X POST https://dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis {_AUTH} {_JSON} \\
  -H 'X-DashScope-Async: enable' \\
  -d '{{"model": "wan2.2-kf2v-flash", "input": {{"prompt": "{{prompt}}", "first_frame_url": "{{first_frame}}", "last_frame_url": "{{last_frame}}"}}}}'
# 图片可以是链接, 也可以是 data:image/png;base64,…; 本地图片 LiteCode 会自动转 base64
# 测试用 (只验证 Key, 不提交任务): 查一个不存在的任务
curl -s https://dashscope.aliyuncs.com/api/v1/tasks/litecode-test {_AUTH}""",
       "https://help.aliyun.com/zh/model-studio/image-to-video-api-reference"),
    _p("minimax_hailuo", "MiniMax · 海螺视频", "video", "http",
       "打开 MiniMax 开放平台 (platform.minimax.cn) → 用户中心「接口密钥」→「创建新的 API Key」。按量收费。",
       f"""MiniMax 海螺生视频 (按量收费, 异步: 提交 → 查状态 → 换下载地址)。
Key: 粘贴你的 Key
# ① 文生视频: 提交任务, 返回 task_id
curl -s -X POST https://api.minimax.cn/v1/video_generation {_AUTH} {_JSON} \\
  -d '{{"model": "MiniMax-Hailuo-2.3", "prompt": "{{prompt}}", "duration": 6, "resolution": "768P"}}'
# ② 查状态: 每隔 10 秒查一次, status 为 Success 就完成并给出 file_id; Fail 表示失败
curl -s 'https://api.minimax.cn/v1/query/video_generation?task_id={{task_id}}' {_AUTH}
# ③ 用 file_id 换下载地址 (在 file.download_url)
curl -s 'https://api.minimax.cn/v1/files/retrieve?file_id={{file_id}}' {_AUTH}
# 图生视频 (图片作首帧, 字段 first_frame_image): 同样 ①②③
curl -s -X POST https://api.minimax.cn/v1/video_generation {_AUTH} {_JSON} \\
  -d '{{"model": "MiniMax-Hailuo-2.3", "prompt": "{{prompt}}", "first_frame_image": "{{first_frame}}"}}'
# 首尾帧 (MiniMax-Hailuo-02, 不支持 512P)
curl -s -X POST https://api.minimax.cn/v1/video_generation {_AUTH} {_JSON} \\
  -d '{{"model": "MiniMax-Hailuo-02", "prompt": "{{prompt}}", "first_frame_image": "{{first_frame}}", "last_frame_image": "{{last_frame}}"}}'
# 参考生视频 (人物主体参考, S2V-01; 目前只支持一张人脸图)
curl -s -X POST https://api.minimax.cn/v1/video_generation {_AUTH} {_JSON} \\
  -d '{{"model": "S2V-01", "prompt": "{{prompt}}", "subject_reference": [{{"type": "character", "image": ["{{image_url}}"]}}]}}'
# 图片可以是链接, 也可以是 data:image/jpeg;base64,…; 本地图片 LiteCode 会自动转 base64
# 测试用 (只验证 Key, 不提交任务): 查一个不存在的任务
curl -s 'https://api.minimax.cn/v1/query/video_generation?task_id=litecode-test' {_AUTH}""",
       "https://platform.minimax.cn/docs/api-reference/video-generation-t2v"),
    _p("kling", "可灵 Kling", "video", "http",
       "在可灵开放平台申请 API Key (以官方页面为准)。接口路径和字段名请按官方文档写进下面这段话, LiteCode 会照着调用。",
       """可灵生视频。
Key: 粘贴你的 Key
# 鉴权: Authorization: Bearer <API Key> (或用 AK/SK 生成 JWT)
# 文生视频 / 图生视频 / 首尾帧的接口路径与字段名以官方文档为准:
# https://kling.ai/document-api/api/get-started/authentication.md""",
       "https://kling.ai/document-api/api/get-started/authentication.md", verified=False),
    _p("comfyui_h3", "ComfyUI · MiniMax H3 视频 (自建)", "video", "comfyui",
       "自建 ComfyUI (开源免费, 用自己的显卡), 需要装好 H3 视频模型。启动时加 --listen 参数, 把地址填到下面, 如 10.0.0.5:8188。",
       """自建 ComfyUI 生视频 (开源免费)。
地址: 127.0.0.1:8188
# 测试: 看 ComfyUI 版本和显卡
curl -s {base_url}/system_stats
# 出视频用 comfyui-video 技能的脚本; 素材写本地文件或链接, 本地文件自动上传; 宽高 32 的倍数, 帧数 17n+5
C="python3 ${LITECODE_HOME:-/opt/litecode}/skills/comfyui-image/scripts/comfy.py"
$C run {base_url} -w h3_t2v --prompt '{prompt}' --frames 73                                       # 文生视频
$C run {base_url} -w h3_i2v --input first={first_frame} --prompt '{prompt}'                       # 图生视频 (首帧)
$C run {base_url} -w h3_fl2v --input first={first_frame} --input last={last_frame} --prompt '{prompt}'   # 首尾帧
$C run {base_url} -w h3_r2v --input ref_image={image_url} --prompt '<Picture 1> 里的人{prompt}'     # 参考生视频
# 线上机先等队列空; 详见 comfyui-video 技能""",
       "skills/comfyui-video/SKILL.md"),
    # ══ 语音合成 ══
    _p("minimax_tts", "MiniMax · 语音合成 / 声音复刻", "tts", "http",
       "MiniMax 开放平台 (platform.minimax.cn) → 用户中心「接口密钥」创建 Key。按量收费。",
       f"""MiniMax 语音合成 (按量收费)。
Key: 粘贴你的 Key
# 普通朗读: 结果 data.audio 是 hex 编码的音频 (bytes.fromhex 后存 mp3); output_format 改成 url 则返回链接
curl -s -X POST https://api.minimax.cn/v1/t2a_v2 {_AUTH} {_JSON} \\
  -d '{{"model": "speech-2.8-hd", "text": "{{text}}", "stream": false, "voice_setting": {{"voice_id": "male-qn-qingse", "speed": 1, "vol": 1, "pitch": 0}}, "audio_setting": {{"format": "mp3"}}, "output_format": "url"}}'
# 指定音色: 把 voice_id 换成平台音色列表里的 ID (以官方文档为准)
# 声音复刻 (参考音频 {{ref_audio}}, 10 秒~5 分钟): ① 上传音频拿 file_id
curl -s -X POST https://api.minimax.cn/v1/files/upload {_AUTH} -F purpose=voice_clone -F 'file=@{{ref_audio}}'
# ② 复刻成自定义 voice_id, 之后朗读时 voice_setting.voice_id 填它 (字段以官方文档为准)
curl -s -X POST https://api.minimax.cn/v1/voice_clone {_AUTH} {_JSON} \\
  -d '{{"file_id": {{file_id}}, "voice_id": "{{my_voice_id}}"}}'""",
       "https://platform.minimax.cn/docs/api-reference/speech-t2a-http"),
    _p("dashscope_cosyvoice", "阿里云百炼 · CosyVoice 语音合成", "tts", "http",
       "百炼控制台「API Key」页面创建 Key (bailian.console.aliyun.com)。按量收费, 仅北京地域。",
       f"""阿里云百炼 CosyVoice 语音合成 (按量收费)。
Key: 粘贴你的 Key
# 普通朗读: 结果 output.audio.url, 下载即可
curl -s -X POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer {_AUTH} {_JSON} \\
  -d '{{"model": "cosyvoice-v3-flash", "input": {{"text": "{{text}}", "voice": "longanyang", "format": "mp3", "sample_rate": 24000}}}}'
# 指定音色: 改 voice (音色列表以官方文档为准); 声音复刻需先用参考音频创建音色, 步骤以官方文档为准""",
       "https://help.aliyun.com/zh/model-studio/cosyvoice-tts-http-api"),
    _p("openai_tts", "OpenAI 兼容 · 语音合成", "tts", "openai_tts",
       "任何兼容 OpenAI /audio/speech 的服务: 填地址 (通常以 /v1 结尾) 和 Key。",
       f"""OpenAI 兼容语音合成。
地址: https://api.openai.com/v1
Key: 粘贴你的 Key
# 普通朗读: 响应直接是音频
curl -s {{base_url}}/audio/speech {_AUTH} {_JSON} \\
  -d '{{"model": "gpt-4o-mini-tts", "input": "{{text}}", "voice": "coral", "response_format": "mp3"}}' -o out.mp3
# 指定音色: 改 voice; 参考音频克隆 ({{ref_audio}}) 不是标准字段, 自建服务按其文档写""",
       "https://developers.openai.com/api/docs/guides/text-to-speech"),
    _p("indextts2_gpu", "IndexTTS-2 (gpu-a, 待部署)", "tts", "http",
       "占位卡: gpu-a 上的 IndexTTS-2 还在部署, 端口待定。部署好后把真实地址和 curl 填到下面。",
       """IndexTTS-2 语音合成 / 参考音频克隆 (自建, 待部署)。
地址: http://198.51.100.16:端口待定
# 部署完成后在这里写真实 curl: 普通朗读 {text} / 参考音频克隆 {ref_audio}""", verified=False),
    # ══ 语音识别 ══
    _p("siliconflow_asr", "硅基流动 · 语音识别 (SenseVoice)", "asr", "openai_asr",
       "和硅基流动生图同一个 Key (cloud.siliconflow.cn →「API 密钥」)。音频不超过 1 小时、50MB。",
       f"""硅基流动语音识别。
Key: 粘贴你的 Key
# 上传音频文件 {{audio_file}}, 结果在 text
curl -s https://api.siliconflow.cn/v1/audio/transcriptions {_AUTH} \\
  -F model=FunAudioLLM/SenseVoiceSmall -F 'file=@{{audio_file}}'""",
       "https://docs.siliconflow.cn/cn/api-reference/audio/create-audio-transcriptions"),
    _p("openai_asr", "OpenAI 兼容 · 语音识别", "asr", "openai_asr",
       "任何兼容 OpenAI /audio/transcriptions 的服务 (含自建 whisper): 填地址和 Key。",
       f"""OpenAI 兼容语音识别。
地址: https://api.openai.com/v1
Key: 粘贴你的 Key
# 上传音频文件 {{audio_file}}, 结果在 text
curl -s {{base_url}}/audio/transcriptions {_AUTH} -F model=gpt-4o-mini-transcribe -F 'file=@{{audio_file}}'""",
       "https://developers.openai.com/api/docs/guides/speech-to-text"),
    _p("sensevoice_gpu", "SenseVoice (gpu-a, 待部署)", "asr", "http",
       "占位卡: gpu-a 上的 SenseVoice 还在部署, 端口待定。部署好后把真实地址和 curl 填到下面。",
       """SenseVoice 语音识别 (自建, 待部署)。
地址: http://198.51.100.16:端口待定
# 部署完成后在这里写真实 curl: 上传 {audio_file} 取文字""", verified=False),
    # ══ 搜索 ══
    _p("searxng", "SearXNG (开源自建)", "search", "searxng",
       "开源免费的搜索服务, 自己部署, 不用 Key。LiteCode 部署时通常已自带 (会自动显示为已接好), 这里只需填地址。",
       """SearXNG 搜索 (开源免费)。
地址: http://127.0.0.1:18888
# 搜索: 结果在 results[] 的 title / url / content (需在 settings.yml 的 search.formats 里开启 json)
curl -s 'http://127.0.0.1:18888/search?q={query}&format=json'""",
       "https://docs.searxng.org/dev/search_api.html"),
    _p("bocha", "博查搜索", "search", "http",
       "打开 open.bochaai.com 注册登录, 在控制台创建 API Key (菜单名以官网为准)。收费, 价格见官网。",
       f"""博查 AI 搜索 (国内可用, 收费)。
Key: 粘贴你的 Key
# 搜索: 结果在 webPages.value[] (name / url / snippet / summary); 有的返回外面还包一层 data
curl -s -X POST https://api.bochaai.com/v1/web-search {_AUTH} {_JSON} \\
  -d '{{"query": "{{query}}", "freshness": "noLimit", "summary": true, "count": 8}}'""",
       "https://open.bochaai.com/"),
    _p("tavily", "Tavily 搜索", "search", "http",
       "打开 app.tavily.com 注册登录, 首页就能复制 API Key (tvly- 开头)。官方写明每月 1000 次免费额度, 不用绑卡。",
       f"""Tavily 搜索 (海外, 每月有免费额度)。
Key: 粘贴你的 Key
# 搜索: 结果在 results[] 的 title / url / content / score
curl -s -X POST https://api.tavily.com/search {_AUTH} {_JSON} -d '{{"query": "{{query}}"}}'""",
       "https://docs.tavily.com/documentation/quickstart"),
    # ══ 音乐 ══
    _p("minimax_music", "MiniMax · 音乐生成", "music", "http",
       "MiniMax 开放平台 (platform.minimax.cn) → 用户中心「接口密钥」创建 Key。收费政策以官网为准。",
       f"""MiniMax 音乐生成。
Key: 粘贴你的 Key
# 纯音乐 (BGM): 结果在 data.audio (output_format 为 url 时是下载链接)
curl -s -X POST https://api.minimax.cn/v1/music_generation {_AUTH} {_JSON} \\
  -d '{{"model": "music-2.6", "prompt": "{{prompt}}", "is_instrumental": true, "output_format": "url"}}'
# 带歌词带人声: 加 lyrics (用 \\n 换行, 可写 [Verse] [Chorus] 段落标记), 去掉 is_instrumental
curl -s -X POST https://api.minimax.cn/v1/music_generation {_AUTH} {_JSON} \\
  -d '{{"model": "music-2.6", "prompt": "{{prompt}}", "lyrics": "[Verse]\\n{{lyrics}}", "output_format": "url"}}'
# 风格写进 prompt (如 "轻快的电子乐, 适合产品宣传"); 没有时长字段, 实际时长见返回的 extra_info.music_duration (毫秒)""",
       "https://platform.minimax.cn/docs/api-reference/music-generation"),
    _p("acestep_gpu", "ACE-Step 音乐 (gpu-a, 待部署)", "music", "http",
       "占位卡: gpu-a 上的 ACE-Step 还在部署, 端口待定。部署好后把真实地址和 curl 填到下面。",
       """ACE-Step 音乐生成 (自建, 待部署)。
地址: http://198.51.100.16:端口待定
# 部署完成后在这里写真实 curl: 纯音乐 / 带歌词人声 / 指定时长风格""", verified=False),
    _p("suno", "Suno 类音乐服务", "music", "http",
       "Suno 目前没有官方公开 API (只有合作伙伴计划)。用第三方代理时, 按它的文档把地址、Key、curl 写进下面。",
       """Suno 类音乐服务 (第三方代理)。
Key: 粘贴你的 Key
# 接口路径与字段名按你所用代理服务的文档填写""", verified=False),
    # ══ 向量 / 重排 ══
    _p("openai_embeddings", "向量 (OpenAI 兼容)", "embedding", "openai_embeddings",
       "知识库用, 一般不用管。默认写的是硅基流动 (cloud.siliconflow.cn →「API 密钥」), 自建服务换地址和模型即可。",
       f"""文本向量 (默认硅基流动)。
Key: 粘贴你的 Key
# 结果在 data[0].embedding
curl -s https://api.siliconflow.cn/v1/embeddings {_AUTH} {_JSON} \\
  -d '{{"model": "Qwen/Qwen3-Embedding-8B", "input": "{{text}}"}}'""",
       "https://docs.siliconflow.com/cn/api-reference"),
    _p("siliconflow_rerank", "重排 (/rerank)", "rerank", "openai_rerank",
       "知识库用, 一般不用管。默认写的是硅基流动, 自建 vLLM 等也提供 /rerank。",
       f"""检索重排 (默认硅基流动)。
Key: 粘贴你的 Key
# 结果在 results[] 的 index / relevance_score
curl -s https://api.siliconflow.cn/v1/rerank {_AUTH} {_JSON} \\
  -d '{{"model": "Qwen/Qwen3-Reranker-8B", "query": "{{query}}", "documents": ["文档1", "文档2"], "top_n": 5}}'""",
       "https://docs.siliconflow.com/cn/api-reference"),
    # ══ 通知 ══
    _p("dingtalk_bot", "钉钉群机器人", "notify", "dingtalk_webhook",
       "在钉钉群的「群设置」里找到「机器人」, 添加一个「自定义」机器人 (按钮名以钉钉当前版本为准)。安全设置建议选「加签」: "
       "把 Webhook 地址贴进下面 curl 里, SEC 开头的加签密钥贴到「加签密钥」那一行。",
       """钉钉群机器人, 任务做完把结果发到这个群。
加签密钥: 粘贴 SEC 开头的密钥 (没开加签就删掉这一行; 开了加签 LiteCode 会自动计算签名)
# 发一条文字消息 (curl 写不出加签签名, 带签名的发送由 LiteCode 自动完成)
curl -s 'https://oapi.dingtalk.com/robot/send?access_token=粘贴你的token' -H 'Content-Type: application/json' \\
  -d '{"msgtype": "text", "text": {"content": "{text}"}}'""",
       "https://open.dingtalk.com/document/robots/custom-robot-access"),
    _p("feishu_bot", "飞书群机器人", "notify", "feishu_webhook",
       "飞书群右上角「更多」→「设置」→「群机器人」→「添加机器人」→「自定义机器人」, 添加后复制 Webhook 地址贴进下面 curl。"
       "如果安全设置开了「签名校验」, 把密钥贴到「签名校验密钥」那一行。",
       """飞书群机器人, 任务做完把结果发到这个群。
签名校验密钥: 粘贴密钥 (没开签名校验就删掉这一行; 开了 LiteCode 会自动计算签名)
# 发一条文字消息
curl -s -X POST 'https://open.feishu.cn/open-apis/bot/v2/hook/粘贴你的token' -H 'Content-Type: application/json' \\
  -d '{"msg_type": "text", "content": {"text": "{text}"}}'""",
       "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot"),
    _p("wecom_bot", "企业微信群机器人", "notify", "wecom_webhook",
       "在企业微信群里添加「群机器人」(入口以企业微信当前版本为准), 创建后在机器人详情里复制 Webhook 地址, 贴进下面 curl。每个机器人每分钟最多发 20 条。",
       """企业微信群机器人, 任务做完把结果发到这个群。
# 发一条文字消息; 成功返回 errcode=0
curl -s 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=粘贴你的key' -H 'Content-Type: application/json' \\
  -d '{"msgtype": "text", "text": {"content": "{text}"}}'""",
       "https://developer.work.weixin.qq.com/document/path/91770"),
    _p("smtp", "邮件 (QQ / 163 等 SMTP)", "notify", "smtp",
       "密码处要填「授权码」, 不是邮箱登录密码。QQ 邮箱: 网页版「设置」→「账号」里找到 POP3/IMAP/SMTP 服务并开启, 按提示验证后得到授权码; "
       "163 邮箱: 「设置」→「POP3/SMTP/IMAP」里开启 SMTP 并获取授权码。菜单名以邮箱官方页面为准。",
       """用邮箱发通知邮件。
SMTP 服务器: smtp.qq.com:465   (163 邮箱写 smtp.163.com:465)
发件邮箱: 粘贴你的邮箱
授权码: 粘贴授权码
发给: 粘贴收件人邮箱 (多个用逗号隔开)""",
       "", extra={}),
    # ══ 对象存储 ══
    _p("minio", "MinIO / S3 兼容存储", "storage", "s3",
       "填存储服务的地址、Access Key 和 Secret Key (在存储服务的控制台创建)。下面三项写好即可, 测试会列出存储桶。",
       """S3 兼容存储。
地址: http://127.0.0.1:9000
Access Key: 粘贴 Access Key
Secret Key: 粘贴 Secret Key
区域: us-east-1""",
       "", extra={"region": "us-east-1"}),
    _p("aliyun_oss", "阿里云 OSS", "storage", "s3",
       "在阿里云控制台创建 AccessKey; OSS 的 S3 兼容地址按所在地域填写, 以官方文档为准。",
       """阿里云 OSS (S3 兼容)。
地址: 按官方文档填写所在地域的 S3 兼容地址
Access Key: 粘贴 AccessKey ID
Secret Key: 粘贴 AccessKey Secret
区域: 以官方文档为准""", "", verified=False),
    _p("tencent_cos", "腾讯云 COS", "storage", "s3",
       "在腾讯云控制台创建 API 密钥; COS 的 S3 兼容地址按所在地域填写, 以官方文档为准。",
       """腾讯云 COS (S3 兼容)。
地址: 按官方文档填写所在地域的 S3 兼容地址
Access Key: 粘贴 SecretId
Secret Key: 粘贴 SecretKey
区域: 以官方文档为准""", "", verified=False),
    # ══ 其它 ══
    _p("custom_http", "其他服务 (自己写)", "other", "http",
       "任何服务都行: 用一两句话写清楚它是干什么的, 再贴一条能用的 curl。LiteCode 读了就会照着调用。",
       """这是一个 xxx 服务, 用来 xxx。
Key: 粘贴你的 Key
# 调用示例 ({prompt} {text} 等由 LiteCode 填)
curl -s -X POST https://example.com/api/xxx -H 'Authorization: Bearer {api_key}' -H 'Content-Type: application/json' \\
  -d '{"prompt": "{prompt}"}'"""),
]
