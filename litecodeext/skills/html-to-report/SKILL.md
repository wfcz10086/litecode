---
name: html-to-report
description: >
  HTML 报告/教程/调研单页 → 直接出图分享 (PNG/JPG).
  触发场景: 用户要 "做一个 X 教程, 转成图" / "出一份调研报告, 发给别人看" /
  "写一个 X 介绍, 转成图片" / "X 的步骤图卡" 等任何 "先做单页 HTML, 再转成
  shareable 图片" 的需求.
  关键词触发: 教程 报告 调研 介绍 图卡 海报 长图 分享 微信图 朋友圈图
  说明书 步骤图 单页 网页截图 网页转图 html2img html-to-image 渲染 出图.

  **绝对不能做的事 (anti-pattern, 历史踩坑过)**:
  - ❌ 启 Xvfb + DISPLAY=:99 + 开桌面浏览器 + scrot/import 截屏 (X server lock 冲突死路)
  - ❌ desktop_exec 开 chrome → desktop_screenshot 截桌面 (要 X 环境, 不可靠)
  - ❌ 装 wkhtmltopdf / weasyprint (WebKit 老, CSS 兼容差, 中文渲染差)

  **唯一正确做法**: 调容器内已装的 `node /opt/browser_node/render_html.js`
  (headless chrome + puppeteer-core + fullPage), 一次命令直出图.
---

# HTML → Report Image Skill

## 工作流 (3 步)

### 第 1 步: 写 HTML 到 bind-mount 路径
**必须**写到 `/tmp/litecode_workspace/` 下 (它在 host 是 `./workspace/`,
bind mount 同步, 容器内/外都看得到; 别写 `/tmp/foo.html` 因为容器内 /tmp 是
独立的, host 看不见).

```python
write_file("/tmp/litecode_workspace/reports/egg_fried_rice.html", html_content)
```

### 第 2 步: 一行命令渲染
```bash
node /opt/browser_node/render_html.js \
  /tmp/litecode_workspace/reports/egg_fried_rice.html \
  /tmp/litecode_workspace/reports/egg_fried_rice.png \
  1080
```

参数:
- 位置 1: 输入 HTML 路径
- 位置 2: 输出 PNG/JPG 路径 (省略 = 同名 .png)
- 位置 3: viewport 宽度像素 (常用 1080 微信长图 / 1200 桌面预览 / 900 紧凑卡片)
- `--wait=N` 额外等 N 毫秒 (有 JS 动画/图表时用)
- `--jpeg-quality=N` 出 .jpg 时压缩质量 (默认 88)

输出 stdout 一行 JSON:
```json
{"ok":true,"path":"/tmp/litecode_workspace/reports/egg_fried_rice.png",
 "size":625616,"width":1080,"height":4320,"device_scale":2,"type":"png","ms":2338}
```

### 第 3 步: 回报给用户
告诉用户图片路径 (host 视角): `./workspace/reports/egg_fried_rice.png`
或贴 b64 (小图 < 100KB).

## HTML 设计要点 (出图美观)

### 字体 (必备, 不写中文会被 Times New Roman 替换难看)
```css
body {
  font-family: -apple-system, 'PingFang SC', 'Noto Sans CJK SC',
               'WenQuanYi Micro Hei', sans-serif;
}
```
容器内已装 Noto Sans CJK (SC/TC/JP/HK/KR) + 文泉驿微米黑 + 文泉驿正黑 +
日文 IPA Gothic + Color Emoji. **不会出 □□□**, 直接用就行.

### 长图布局原则 (微信/朋友圈分享典型尺寸 1080w × 不限高)
- 整体宽 1080, 内容居中 max-width 920
- 卡片化分段 (border-radius + box-shadow), 不要一大坨堆一起
- 用 emoji 做章节锚点 (🍳 🥚 🍚 → 视觉锚便于阅读)
- 字号: 标题 36-48px / 正文 18-22px / 注释 14-16px
- 颜色: 不要用纯黑 #000, 用 #1f2937/#374151 等中性深色, 配高饱和强调色
- 间距大方: padding 40-60px, margin 24-40px, line-height 1.7-1.9

### CSS 渐变背景 (撑出"成品感")
```css
body { background: linear-gradient(135deg, #fef3c7, #fde68a); }
/* 暖橙调适合食谱; 调研/报告用 #f3f4f6→#e5e7eb 或 #ddd6fe→#c4b5fd */
```

### 章节卡片基础样式
```css
.section {
  background: #fff;
  border-radius: 16px;
  padding: 40px 48px;
  box-shadow: 0 8px 24px rgba(0,0,0,.08);
  margin-bottom: 24px;
}
```

## 常见问题速查

| 问题 | 原因 | 解决 |
|---|---|---|
| 出图全空白 | viewport 宽度被 CSS @media 命中"手机版" | 显式设 width=1200 |
| 中文出 □□□ | font-family 没列 CJK 字体 | 加 'PingFang SC','Noto Sans CJK SC' |
| 字体糊 | 缺 deviceScaleFactor | 脚本已自动 ×2, 不用管 |
| Web font 没加载 | networkidle 太短 | 加 `--wait=2000` |
| 图表/JS 渲染没出来 | echarts/chart.js 需要时间 | 加 `--wait=3000` |
| 图太大发不出 | PNG 太胖 | 改后缀 .jpg, 加 `--jpeg-quality=80` |
| 路径报"不存在" | 写到 host /tmp 了 | 改写 `/tmp/litecode_workspace/...` |

## 完整示例: 蛋炒饭教程图

```python
# 1) 写 HTML
html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>蛋炒饭</title>
<style>
body { margin: 0; padding: 60px 0; font-family: -apple-system,'PingFang SC','Noto Sans CJK SC',sans-serif;
       background: linear-gradient(135deg,#fef3c7,#fde68a); color: #374151; }
.page { max-width: 920px; margin: auto; padding: 0 40px; }
h1 { font-size: 48px; color: #92400e; margin: 0 0 8px; }
.subtitle { color: #b45309; font-size: 18px; margin-bottom: 32px; }
.card { background: white; border-radius: 16px; padding: 36px 40px;
        box-shadow: 0 8px 24px rgba(0,0,0,.08); margin-bottom: 24px; }
.card h2 { color: #b45309; font-size: 26px; margin: 0 0 16px; }
.step { display:flex; gap:16px; margin:18px 0; line-height:1.8; }
.step-num { flex-shrink:0; width:36px;height:36px;background:#f59e0b;color:white;
            border-radius:50%; display:flex; align-items:center; justify-content:center;
            font-weight:bold; }
</style></head><body>
<div class="page">
  <h1>🍳 蛋炒饭</h1>
  <div class="subtitle">10 分钟出锅 · 隔夜冷饭最香</div>
  <div class="card">
    <h2>🥚 食材</h2>
    <p>冷米饭 1 碗 · 鸡蛋 2 个 · 葱花 适量 · 盐 · 生抽 · 油</p>
  </div>
  <div class="card">
    <h2>🔥 做法</h2>
    <div class="step"><div class="step-num">1</div><div>鸡蛋打散, 加少许盐</div></div>
    <div class="step"><div class="step-num">2</div><div>热锅冷油, 倒入蛋液炒散立刻盛出</div></div>
    <div class="step"><div class="step-num">3</div><div>留底油, 下冷饭翻炒至粒粒分明</div></div>
    <div class="step"><div class="step-num">4</div><div>回锅炒蛋 + 葱花, 沿锅边淋生抽提香</div></div>
    <div class="step"><div class="step-num">5</div><div>翻匀出锅</div></div>
  </div>
</div></body></html>"""
write_file("/tmp/litecode_workspace/reports/egg_fried_rice.html", html)

# 2) 渲染
execute_shell("node /opt/browser_node/render_html.js "
              "/tmp/litecode_workspace/reports/egg_fried_rice.html "
              "/tmp/litecode_workspace/reports/egg_fried_rice.png 1080")

# 3) 回报
# stdout: {"ok":true,"path":"...","width":1080,"height":~1500}
# host 文件: ./workspace/reports/egg_fried_rice.png
```

## 调研报告类布局建议

调研报告 (n+ 个图表 + 表格 + 结论) 推荐 1200 宽, 用以下结构:

```html
<header>  <!-- 标题/作者/日期/封面图 -->
<section class="exec-summary">  <!-- 摘要, 3-5 个核心数字 -->
<section class="data">  <!-- 数据表 + 图表 -->
<section class="analysis">  <!-- 分析段落 -->
<section class="conclusion">  <!-- 结论 + 建议 -->
<footer>  <!-- 数据来源/免责 -->
```

每个 section 一张卡片, 卡片间 32-40px 间距.

图表如果用 echarts/chart.js → 渲染时加 `--wait=3000` 让 chart 画完.
