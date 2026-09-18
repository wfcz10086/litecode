# wechat — 微信附件处理技能

## 触发时机

收到含路径标记的微信消息时自动加载，例如：
- `图片路径: /path/to/xxx.jpg`
- `文件路径: /path/to/xxx.pdf`
- `[之前缓冲的图片]`
- `[收到文件: xxx.pdf]`

## 图片处理

消息格式示例：
```
[收到图片]
图片路径: /root/.litecode/wechat_media/wx_xxx_20240101_abc123.jpg
```

处理步骤：
1. 使用视觉模型识别图片内容（通过 execute_shell 调用视觉 API）
2. 若有多张图片，逐张识别后汇总

```python
# 图片识别示例（execute_shell）
import base64, json, requests
path = "/root/.litecode/wechat_media/xxx.jpg"
b64 = base64.b64encode(open(path,"rb").read()).decode()
# 调用视觉模型
resp = requests.post(VISION_URL, headers=HEADERS, json={
    "model": VISION_MODEL,
    "messages": [{"role":"user","content":[
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
        {"type":"text","text":"请描述这张图片的内容"}
    ]}],
    "max_tokens": 500
})
print(resp.json()["choices"][0]["message"]["content"])
```

## PDF 处理

消息格式示例：
```
[收到文件: report.pdf (1MB)]
文件路径: /root/.litecode/wechat_media/wx_xxx_report.pdf
```

处理步骤：
1. 优先用 `pdftotext`（poppler）提取文字
2. 失败时用 `pypdf` / `pymupdf`

```bash
# execute_shell
pdftotext /root/.litecode/wechat_media/xxx.pdf - | head -200
```

```python
# 或 pypdf
from pypdf import PdfReader
r = PdfReader("/root/.litecode/wechat_media/xxx.pdf")
text = "\n".join(p.extract_text() or "" for p in r.pages)
print(text[:3000])
```

## 文本文件处理

```python
# execute_shell / read_file 工具
content = open("/root/.litecode/wechat_media/xxx.txt", errors="replace").read()
print(content[:5000])
```

## Excel / CSV

```python
import pandas as pd
df = pd.read_excel("/root/.litecode/wechat_media/xxx.xlsx")  # 或 read_csv
print(df.head(20).to_string())
print(df.describe())
```

## 语音（已转写）

语音消息已由 SDK 或 Whisper 转写为文字，直接响应文字内容即可，无需额外处理。

## 视频

```bash
# 获取视频基本信息
ffprobe -v quiet -print_format json -show_format -show_streams /path/to/video.mp4
```

视频内容分析需借助外部工具，通常告知用户视频已保存路径即可。

## 缓冲图片合并

当用户先发多张图，再发文字时，消息格式为：
```
[之前缓冲的图片]
图片路径: /path/img1.jpg
图片路径: /path/img2.jpg
用户的文字说明
```

此时按用户文字意图，对所有缓冲图片统一处理（识别/对比/分析等）。

## 注意事项

- 所有附件下载至 `/root/.litecode/wechat_media/`，文件名含 bot_id + 时间戳
- 回复微信时避免 Markdown 格式（`**粗体**`、`# 标题`），用纯文本
- 长内容分段回复，每段不超过 500 字
- 图片/文件已在本地，直接用路径操作，无需重新下载
