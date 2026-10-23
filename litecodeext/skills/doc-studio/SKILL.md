---
name: doc-studio
description: >
  文档工坊: 做 PPT / 幻灯片 / 演示文稿 / 报告 / Word 文档 / PDF / 海报 / 宣传册 / 长图, HTML 优先设计,
  配真实照片 (开放授权图库, 自动署名)、AI 插画、图表, 再导出 PDF、可编辑 PPTX、DOCX、长图, 最后截图质检。
  要好看的成品走本技能; 编辑现有 .pptx/.docx 或要求原生对象时用 pptx / docx 技能。
aliases: [做PPT, 做ppt, 幻灯片, 演示文稿, 做报告, 做文档, Word文档, 生成PDF, 海报, 宣传册, 长图]
---

# 文档工坊 (HTML 优先, 多技能协作)

先用 HTML 把版面做漂亮 (浏览器排版最强), 再导出各种格式。每一步都是独立脚本, 输入输出是文件/JSON,
对话里按顺序跑, 也能在 DAG 里每步一个节点 (预设 Pipeline「📑 文档工坊」)。

```bash
D=${LITECODE_HOME:-/opt/litecode}/skills/doc-studio
P=/tmp/litecode_workspace/<项目名>        # HTML、img/、导出文件都放这里
cp $D/assets/base.css $P/                  # 设计 tokens + 幻灯片/A4 版式 + 打印规则 (脚本也会自动补)
```
**`D=` 这一行原样写进每条 execute_shell 命令, 让 shell 展开** (桌面版/测试实例装在别处, LITECODE_HOME 会指过去),
别自己替换成 /opt/litecode。脚本找不到时先 `echo $LITECODE_HOME; ls $D/scripts` 定位,
**不要自己另写配图/导出脚本** —— images.py 已处理图库限流、授权过滤、裁剪和署名。
成品路径 (PDF / PPTX / DOCX / 长图 / 截图) **写进回复**, 网页会出预览卡片。

## Keywords
ppt pptx 幻灯片 演示文稿 路演 汇报 课件 报告 文档 word docx pdf 海报 宣传册 画册 长图 封面 配图 照片 插画 图表 排版 版式 导出 主题 模板

## 流程 (按顺序, 别跳)

### 1. 内容 (大纲)
- 先定: 受众、页数 (用户没说: PPT 8~12 页, 文档 2~4 页)、主线 (结论先行)。
- 需要事实/数据 → `web_search` (时效内容必须搜) 或 `kb_search` (用户知识库); 数字要注明来源年份, 不确定的不写。
- 每页写: 标题 (一句话观点) + 2~4 个要点 + 配图需求 (照片拍什么 / 图表画什么数据)。

### 2. 设计 (HTML)
- 主题: 用户指定风格 → 照 `theme-factory/themes/*.md` 的配色字体; 否则按内容选 (旅游=自然色, 科技=深蓝+亮色, 商务=深灰+一个强调色)。
  只改 `:root` 的 `--ds-primary / --ds-accent / --ds-bg / --ds-text …`, 全文统一 tokens, 别到处写死颜色。设计原则参考 frontend-design 技能。
- **幻灯片**: 每页一个 `<section class="slide">` (固定 1280×720), 参照 `$D/templates/slides_example.html`。
  版式要有变化: 封面 (整页照片+渐变压暗+大标题) / 左文右图 / 三栏卡片 / 大数字 KPI / 图表页 / 时间线 / 结尾页。
  一页一个观点, 正文 ≥ 18px, 每页 ≤ 220 字; 演讲稿写进 `<aside class="notes" style="display:none">` (进 PPT 备注)。
- **文档**: 每页一个 `<section class="page">` (A4, 210×297mm), 参照 `$D/templates/doc_example.html`; 标题用 h1/h2/h3, 表格用 `<table>`, 配图用 `<figure>+<figcaption>`。
- 为了能导出**可编辑 PPTX**: 装饰用真实元素 (div 色块、border), 别用 `::before/::after`; 文字别旋转; 图标用 data-kind="icon" 占位。
- 图片先占位, 别自己编 URL:
  ```html
  <img data-need="西湖断桥残雪, 冬日湖面" data-kind="photo" data-query="West Lake Hangzhou Broken Bridge">
  <img data-need="游客量" data-kind="chart" data-chart='{"type":"bar","labels":["2021","2022","2023"],"series":[{"name":"游客","data":[5,6,8]}],"unit":"千万人次","highlight":2}'>
  <img data-need="水墨风西湖插画" data-kind="illustration" data-query="Chinese ink painting of West Lake">
  <span data-need="交通" data-kind="icon" data-icon="map-pin"></span>
  ```
  - `data-query` 写**英文、具体**的地名/物体 (图库按它搜; "West Lake Hangzhou Leifeng Pagoda" 好, "美丽风景" 差)。
  - 照片放在**有高度**的容器里 (`.photo{height:300px}` 或 flex:1 撑满; 里面 `img{width:100%;height:100%;object-fit:cover}`),
    脚本按版面尺寸裁剪。空 div 当背景图占位时没给高度就是 0px, 图显示不出来 (images.py / qa.py 都会报)。
  - 深色照片上的字用白色 + 渐变遮罩 (见模板封面); 主题深色当标题色放在深色照片上会看不清 (qa.py 按截图像素算对比度)。
  - 块元素也能占位 (`<div class="hero" data-need=… data-kind="photo">` → 设成背景图)。
  - 署名位: 图片所在 figure 里放 `<span data-credit></span>`, 或页脚放 `<span data-credit-page></span>`;
    末页/文末放 `<div class="credits" data-credits></div>` 列出全部图片来源 (CC BY / BY-SA 要求署名, 必须有)。
  - 图表类型: bar / barh / line / area / pie / donut; 多系列给多个 series。图表 = 透明 PNG, 跟页面配色。
  - 流程图 / 架构图 / 时序图 / 思维导图 / 时间线这类**示意图**: 先用 `diagram` 技能把 Mermaid 或 Graphviz 源码渲染成 SVG (深色页加 `--theme dark --bg transparent`), 再用普通 `<img src="…svg">` 放进页面; 不要用 AI 生图画流程图 (文字会乱)。

### 3. 配图
```bash
python3 $D/scripts/images.py $P/deck.html [--assets 用户素材目录] [--comfy host:port|last] [--verify]
```
优先级: ① `--assets` 用户提供/会话附件/知识库导出的图片 (`data-asset="文件名"` 指定, 否则按文件名关键词匹配)
② 照片: 开放授权图库 (默认 openverse→wikimedia, 只收 CC0/公有领域/CC BY/CC BY-SA, 自动写作者/授权/链接)
③ 插画: 有 ComfyUI 地址时调 comfyui-image 技能的 comfy.py 生成 (没给地址就降级找照片, 再不行用主题色色块)
④ 图表: matplotlib ⑤ 图标: 内联 SVG (`data-icon`: map-pin sun leaf water mountain camera clock calendar users chart trend car battery bolt globe shield cpu code chat rocket book heart building plane money cloud tea boat check star)。
- 结果清单 `$P/images.json` (每张图的来源/作者/授权/耗时); 再跑只补没填的, `--force` 全部重取。
- `--verify` 用视觉模型 (同 vision_ocr 的路由) 判断图文是否相符, 不符换下一张; 关键页 (封面、景点页) 建议开。
- 用户发来的照片/会话附件: 先存到 `$P/assets/` 再 `--assets $P/assets`。

### 4. 渲染导出
```bash
python3 $D/scripts/render.py $P/deck.html --pdf $P/deck.pdf --png $P/shots      # PDF (16:9 或 A4 自动) + 每页截图
python3 $D/scripts/render.py $P/deck.html --long $P/deck_long.jpg               # 长图 (手机分享)
python3 $D/scripts/html2pptx.py $P/deck.html -o $P/deck.pptx                    # 可编辑 PPTX (--mode auto|editable|image)
python3 $D/scripts/html2docx.py $P/doc.html -o $P/doc.docx                      # DOCX (--engine auto|pandoc|python)
```
- html2pptx `auto`: 逐页还原成原生文字框/图片/色块/表格; 渐变、滤镜、SVG、图表截成图片贴回原位;
  元素过多或大面积只能截图的页改用高保真模式 (整页图 + 隐藏文字可搜索 + 文字进备注)。PPT 里中文字体默认映射为微软雅黑 (`--font` 改)。
- html2docx: 栅格 (.grid-2/.grid-3) 在 python 引擎里变成无框表格并排, pandoc 引擎会顺序排列; 要版式更像 HTML 用 `--engine python`。
- 海报/宣传册/长图: 单个 `section.page` 或自定尺寸的 section, 只导 PDF/PNG/长图即可。

### 5. 质检 (最多修 2 轮)
```bash
python3 $D/scripts/qa.py $P/deck.html --shots $P/qa_shots -o $P/qa.json [--vision]
```
- 规则: 文字溢出/被裁切/超出页面、文字重叠、空白页、图片没加载/占位没填、字太小、字太多、对比度低; `--vision` 再让视觉模型看截图。
- **自己看截图** (vision_ocr 一次最多几张): 图文是否相符、留白是否平衡、有没有难看的地方。
- 有 error → 改 HTML (删字/拆页/调尺寸/换 data-query 重跑 images.py --force) → 重新导出 → 再 qa; **最多 2 轮**,
  第 2 轮还有问题就把剩下的问题如实告诉用户, 别无限循环。

## 交付
回复里列: 各文件路径、页数、配图来源 (几张网图 + 授权类型 / 几张生成 / 几张图表)、质检结论与已知问题。

## 和其它技能的关系
- 编辑已有 .pptx / .docx、要求原生图表对象 → pptx / docx 技能。
- 纯排版海报 (不配图) 也可走 canvas-design; 长篇研究报告的写作方法看 deep-report, 版面交给本技能。
- 插画生成: comfyui-image (需要 ComfyUI 地址); 本技能只调它的脚本, 不重复实现。

## 排错
- 照片全是色块: 看 `images.json` 的 `sources` (Openverse 本机出口被 Cloudflare 403, 正常; Wikimedia 失败多半是网络或限流, 隔几分钟再跑)。
- 搜到的照片不相关: 换更具体的英文 `data-query` (地名+地标), 加 `--verify`。
- PPTX 文字换行和 HTML 不一样: Office 字体宽度不同, 文字框已留 3% 余量; 仍挤就在 HTML 里少写几个字, 或该页用 `--mode image`。
- 浏览器锁: 同一时间只开一个 chromium (文件锁 /tmp/doc_studio_browser.lock), 用完即关。
