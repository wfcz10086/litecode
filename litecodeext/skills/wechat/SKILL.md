# wechat — 微信附件处理 & 主动发送技能

## 触发时机

收到含路径标记的微信消息时自动加载，例如：
- `图片路径: /path/to/xxx.jpg`
- `文件路径: /path/to/xxx.pdf`
- `[之前缓冲的图片]`
- `[收到文件: xxx.pdf]`

---

## ⭐ 主动发送图片/文件到微信（重要）

Bridge 识别回复文本中的特殊标记，自动将本地文件发送给微信用户。

### 发送图片

```
[发送图片: /tmp/openclaw_workspace/uploads/screenshot.png]
```

完整示例（截图并发送）：
```python
# execute_shell — 截图
import asyncio, sys
sys.path.insert(0, '/opt/litecode/skills/browser-automation')
from browser import BrowserClient
client = BrowserClient()
b64 = asyncio.run(client.screenshot())

import base64
from pathlib import Path
save_path = '/tmp/openclaw_workspace/uploads/screenshot.png'
Path(save_path).write_bytes(base64.b64decode(b64))
print(f'截图已保存: {save_path}')
```

然后在回复文本中写：
```
截图如下：
[发送图片: /tmp/openclaw_workspace/uploads/screenshot.png]
```

### 发送文件（PDF/Excel/任意文件）

```
报告已生成，发送给你：
[发送文件: /tmp/openclaw_workspace/report.pdf]
```

### ⚠️ 注意事项

1. **截图必须保存到 `/tmp/openclaw_workspace/uploads/` 或 `/tmp/`**，不要用 `/root/` 等目录
2. 标记必须在回复正文中，Bridge 会自动拦截并用 SDK 发送，不会显示给用户
3. 每条消息可包含多个发送标记，会依次发送
4. 图片支持：png / jpg / jpeg / gif / webp / bmp
5. 文件支持：任意格式（pdf / xlsx / docx / zip 等）

---

## 接收处理：图片识别

收到图片消息格式：
```
[收到图片]
图片路径: /tmp/openclaw_workspace/uploads/wx_xxx.jpg
```

用视觉模型识别内容：
```python
import base64, json, requests
path = "/tmp/openclaw_workspace/uploads/wx_xxx.jpg"
b64 = base64.b64encode(open(path, "rb").read()).decode()
# 调用视觉模型
resp = requests.post(VISION_URL, headers=HEADERS, json={
    "model": VISION_MODEL,
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": "请描述这张图片的内容"}
    ]}],
    "max_tokens": 500
})
print(resp.json()["choices"][0]["message"]["content"])
```

---

## 接收处理：PDF

```
[收到文件: report.pdf (1MB)]
文件路径: /tmp/openclaw_workspace/uploads/wx_xxx_report.pdf
```

```bash
# execute_shell
pdftotext /tmp/openclaw_workspace/uploads/xxx.pdf - | head -200
```

```python
from pypdf import PdfReader
r = PdfReader("/tmp/openclaw_workspace/uploads/xxx.pdf")
text = "\n".join(p.extract_text() or "" for p in r.pages)
print(text[:3000])
```

---

## 接收处理：Excel / CSV

```python
import pandas as pd
df = pd.read_excel("/tmp/openclaw_workspace/uploads/xxx.xlsx")
print(df.head(20).to_string())
print(df.describe())
```

---

## 接收处理：语音

语音消息已由 SDK 或 Whisper 转写为文字，直接响应文字内容即可，无需额外处理。

---

## 接收处理：视频

```bash
ffprobe -v quiet -print_format json -show_format -show_streams /path/to/video.mp4
```

视频内容分析需借助外部工具，通常告知用户视频已保存路径即可。

---

## 缓冲图片合并

用户先发多张图，再发文字时：
```
[之前缓冲的图片]
图片路径: /path/img1.jpg
图片路径: /path/img2.jpg
用户的文字说明
```

按用户文字意图，对所有缓冲图片统一处理。

---

## 回复格式规范

- 回复微信时**避免 Markdown 格式**（`**粗体**`、`# 标题`），用纯文本
- 长内容分段回复，每段不超过 500 字
- 图片/文件已在本地，直接用路径操作，无需重新下载
