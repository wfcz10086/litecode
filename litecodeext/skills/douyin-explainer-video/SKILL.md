---
name: douyin-explainer-video
description: >
  竖版动漫风解说短视频一条龙 (抖音 / 视频号 / 快手 / 小红书 / B站竖屏):
  选题 → 文案口播 → edge-tts 配音 → 时间轴动画 → 逐帧渲染 → BGM 混音 → final.mp4 + 封面 + 发布包.
  触发场景: "做个抖音视频" "做一条科普短视频" "人物故事视频" "历史/财经/商业/冷知识 解说"
  "天才交易员那种视频" "帮我涨粉做内容" "AI 做视频发抖音" "做个 1 分多钟的竖屏视频".
  关键词: 抖音 视频号 快手 短视频 竖版 竖屏 科普视频 解说 口播 旁白 配音 动漫 漫画风 人物传记
  历史 财经 冷知识 涨粉 爆款 文案 钩子 封面 发布包 mp4 tts 配乐 BGM.
  不适用: 剪辑真人素材 / 需要真实视频画面的 vlog.
---

# 竖版解说短视频 (天才交易员模板)

样板成片: `examples/livermore/` —《天才交易员的陨落》85s, 1080x1920, 7 场, 封面 + 发布包。
全程本地工具, 无版权素材: 画面是 HTML/CSS 动画, 配音 edge-tts, BGM numpy 合成。

## 目录约定
```
SK=<本 skill 目录>          # 容器内 /opt/litecode/skills/douyin-explainer-video
P=/tmp/litecode_workspace/<项目名>   # 项目目录, 所有命令在 P 里跑
```

## 流程 (用 task_create 建好再逐个推进)

1. **核对事实 + 写文案** → `$P/script.json` (先读 `copywriting.md`)。
   **先查资料再动笔**: 年份 / 金额 / 次数 / 名言逐条写进 `facts` (带 source), 查不到可靠出处的说法就不写。
2. **配画面**: `cp $SK/templates/video.html $SK/templates/engine.js $P/`; 选一套配色预设 + 声音组合 (见下, 别和上一条片子撞);
   题材专属画面写 `custom.js` (见下)。通用版式够用时不写也行, 但**题材画面是质感的来源**。
3. **一键跑** (background=true): `cd $P && python3 $SK/scripts/run.py`
   依次: fact 事实闸 → tts 配音 → check 版面自检 → render 逐帧 → bgm → mix → final → qa 成片抽帧。
   任一关卡失败立即停, 日志写明哪步、怎么修; 修完 `run.py --from <那一步>` 接着跑。
   等渲染时并行做 4/5, 别干等。
4. **封面** 1080x1440: 参照 `examples/livermore/cover.html` 写 `cover.html`,
   `python3 $SK/scripts/shot.py cover.html cover.png 1080 1440` (自带版面自检), 再 vision_ocr 打分 ≥8。
5. **发布包** `publish.md`: 标题 x3 + 正文 + 话题 8-10 个 + 置顶评论 + checklist (见 `examples/livermore/publish.md`)。
   标题和正文里的数字同样只能来自 facts。
6. **成片终检 (交付前必做)**: `vision_ocr qa/final_grid.png` (每行一场, 30%/60%/90% 三帧) 逐行查:
   文字互压 / 被图形挡 / 出屏 / 乱码 / 画面大片空白 / **同一场三帧字幕一样 (字幕卡住)**。
   有问题 → 改 → `run.py --from check` → 再查。全过才汇报交付。

## 三道关 (都是脚本硬闸, 不靠自觉)
| 关 | 脚本 | 抓什么 | 来历 |
|---|---|---|---|
| 事实闸 | `factcheck.py` (+ `numcheck.py`) | 旁白年份不在 facts / "N年后"算错 / "第N次"没核实 / 金额对不上 / **画面数字与旁白数字矛盾** | 原片把 1929→1940 写成"十四年后"; 样片画面「428 秒」旁白「约八分二十秒」 |
| 版面自检 | `render.py check` | 配色对比度 (WCAG AA) + 每场 4 个时刻量实际文字框: 互相压住 / 出屏 / 撑破框 / **字幕卡住** | 原片印章叠印章、金句压纸条、每场字幕卡第一句 |
| 成片终检 | `qa_final.py` + vision | 规格 (1080x1920/有音轨/时长) + 抽帧拼图人眼复查 | 预览帧只截中点, 后改的遮挡看不到 |

**画面数字闸 (`numcheck.py`, 事实闸里自动跑, 也可单独 `python3 $SK/scripts/numcheck.py`)**:
- 画面上的数 = engine.js 真画出来的字段: hook.number/title/tape、headline.kicker/title/lines、stamps.title/stamps、
  counter.label/from/to/note、versus 两边 name/value、quote.quote、image.caption、场景 `act`; 数字参数按 `num()` 拼成
  "prefix + 千分位整数 + suffix" 再解析 (所以 **带单位就写进 prefix/suffix**, 如 `"suffix": " 秒"`)。custom.js 版式扫 params 里的字符串和 number/value/to 等键。
- 中英文数字、万/亿/k/M、单位 (秒/分钟/小时/天/年、米/公里、元/块/美元、%/百分之、岁/倍…)、速度 (30万公里/秒 = 每秒三十万公里)、
  时长组合 (八分二十秒 / 1 小时 20 分 / 两分半) 都换算到同一量纲比; "约/左右" 容差 ±6%, "多/超过" 是下限, "不到/近" 是上限, 精确数 ±1.5%。
- 同一场同一量纲: 画面数跟本场旁白对不上且全片旁白和 facts 都没有它 → **硬错误**; 画面数有出处但本场旁白念了一个画面没有、facts 也没有的同类数 → 硬错误 (改旁白);
  旁白没提这类数且无出处 → 只提示。故意不一致的画面字段 (比如梗图里的假数字) 在场景写 `"numcheck_ignore": ["那段文字片段"]`。

故意叠放的元素 (印章盖在行情牌上、整屏遮罩卡、滚动警示带、金币堆) 加 `data-qa="overlay"` 豁免版面自检。

## script.json
```json
{
 "title": "天才交易员的陨落",
 "topic": "财经人物 悲剧",              // 可选: 自动选配色时一起参考的关键词
 "theme": "burgundy",                   // 配色预设名 (见下表); 不写 = 按 title/topic/mood 自动挑
 "bg_style": "radial",                  // 可选: 覆盖预设推荐的背景样式
 "mood": "dark",                        // BGM 情绪; 不写 = 用预设推荐的
 "voices": {"narrator": "云健", "quote": "晓晓"},   // 多角色; 只要一个声音时写 "voice": "zh-CN-YunxiNeural"
 "rate": "+14%", "gap": 0.7, "prosody": "auto",
 "facts": [{"year": 1929, "fact": "股灾期间做空净赚约一亿美元", "source": "Wikipedia"}],   // 事实闸对账用
 "highlight": {"gold": ["一亿美元"], "red": ["破产"]},   // 字幕关键词上色 (gold→accent 色, red→alert 色)
 "scenes": [
  {"id": "s0", "act": "序章 · 1929", "type": "hook",
   "params": {"number": 100000000, "prefix": "$", "title": "天才交易员的陨落", "tape": "BLACK THURSDAY"},
   "text": "旁白全文, 一场 8-16 秒 (约 40-80 字)"},
  {"id": "s5", "type": "quote", "role": "quote", "params": {"quote": "..."}, "text": "引用人物原话的这一场换声音"}
 ]
}
```
旧写法 `"theme": {"bg": "#0a1226", "accent": "#f5c451", "alert": "#e63946"}` 仍然有效 (以 midnight 为底覆盖);
也可以 `{"preset": "mint", "accent": "#0b7a56"}` 在预设上微调。

### 配色预设 (`templates/themes.json`) —— **不同视频不要总用同一套**
| 预设名 | 中文名 | 色样 | 适合题材 | 推荐背景 / BGM |
|---|---|---|---|---|
| midnight | 墨夜蓝 (经典) | 深藏青底, 钢蓝强调, 朱红警示 | 人物陨落 / 悲剧 / 悬疑 | radial / dark |
| deepsea | 深海科技蓝 | 深海蓝底, 电光青强调 | 科技 / AI / 互联网 / 芯片 | blueprint / tech |
| cyber | 赛博霓虹紫 | 暗紫底, 霓虹粉 + 青 | 未来 / 游戏 / 黑客 / 都市传说 | mesh / tech |
| forest | 森林墨绿 | 墨绿底, 嫩叶绿 + 橙 | 自然 / 动物 / 环保 / 地理 | bokeh / calm |
| sunset | 暖阳橙红 | 焦糖棕底, 落日橙 + 玫红 | 美食 / 旅行 / 运动 / 逆袭 | stripes / upbeat |
| sakura | 樱花粉白 (浅底) | 樱粉白底, 莓红 + 紫红 | 情感 / 生活方式 / 治愈 / 节日 | bokeh / warm |
| mono | 极简黑白 | 纯黑底白字, 一点信号红 | 设计 / 哲学 / 犯罪纪实 / 严肃 | stripes / dark |
| newspaper | 复古报纸米色 (浅底) | 旧报纸米黄底, 墨黑字, 铁锈红 | 历史 / 人物传记 / 朝代 / 民国 | paper / calm |
| glacier | 冰川青 | 深冰蓝绿底, 冰川青 + 粉红 | 冷知识 / 物理 / 海洋 / 气候 | mesh / calm |
| burgundy | 酒红金 | 酒红底, 香槟金 + 珊瑚 | 商业大亨 / 奢侈品 / 王朝 | radial / epic |
| mint | 薄荷清新 (浅底) | 薄荷白底, 深松绿 + 西瓜红 | 健康 / 生活小知识 / 教育 | stripes / light |
| cloud | 晨雾灰白 (浅底) | 雾灰白底, 墨蓝字, 宝石蓝 | 科普 / 数码 / 职场清单 | blueprint / tech |

- 自动挑选: 标题/topic 命中关键词 (科技/AI → deepsea·cyber·cloud, 历史/人物 → newspaper·burgundy·midnight,
  美食/生活 → sunset·mint·sakura, 悲剧/悬疑 → 深色系 …) 后在候选里按标题哈希挑; 都没命中按 mood, 再不行按标题哈希。
  同一脚本结果稳定, 不同脚本自然分散。`python3 $SK/scripts/theme.py script.json` 看选中了哪套。
- **对比度硬闸**: 版面自检 (check) 会校验实际使用的颜色: 字幕/正文对背景 ≥ 4.5:1, 强调/警示色对背景 ≥ 3:1 (WCAG AA),
  不达标直接报错说明哪一对颜色差多少。自定义颜色前先跑 `theme.py --list` 看预设的数值做参考。
- 模板 `video.html` 里所有颜色都是主题变量 (卡片底 / 结尾卡文字 / 阴影 / 暗角 …), 浅色预设可以直接用; 写 custom.js/css 时也用
  `var(--ink)` `var(--paper)` `var(--accent)` `var(--panel)` 这些变量, 别写死 `#fff` / `#000`。

### 背景样式 `bg_style` (纯 CSS, 动画只由 seek 时间驱动)
`radial` 径向渐变+浮尘光点 · `blueprint` 蓝图网格线缓慢平移 · `stripes` 斜条纹流动 · `bokeh` 柔光大光斑 ·
`paper` 纸张噪点纹理 · `mesh` 三团色块渐变网格漂移

### 配音: 声音目录 / 多角色 / 韵律
`python3 $SK/scripts/voices.py` 打印目录。可写正式名、中文名或拼音 (`"云健"` `"yunjian"`), 写错会提示最接近的并回退云希。
| 声音 | 正式名 | 人设 | 适合 |
|---|---|---|---|
| 云希 | zh-CN-YunxiNeural | 阳光讲故事男声 (默认) | 人物故事 / 通用解说 |
| 云健 | zh-CN-YunjianNeural | 沉稳有力男声 | 商战 / 体育 / 悲剧纪录 |
| 云扬 | zh-CN-YunyangNeural | 新闻播音男声 | 财经 / 科技新闻 / 严肃科普 |
| 云夏 | zh-CN-YunxiaNeural | 少年音 | 儿童科普 / 搞笑角色 |
| 晓晓 | zh-CN-XiaoxiaoNeural | 温暖知性女声 | 情感 / 生活 / 引用原话 |
| 晓伊 | zh-CN-XiaoyiNeural | 活泼女声 | 美食 / 冷知识 / 种草 |
| 晓北 / 晓妮 | zh-CN-liaoning-XiaobeiNeural / zh-CN-shaanxi-XiaoniNeural | 东北话 / 陕西话 (彩蛋) | 吐槽 / 地方美食 |
| 晓佳 / 云龙 | zh-HK-HiuGaaiNeural / zh-HK-WanLungNeural | 粤语女 / 男 (彩蛋) | 港风题材 / 粤语金句 |
| 晓臻 / 云哲 | zh-TW-HsiaoChenNeural / zh-TW-YunJheNeural | 台湾腔女 / 男 | 生活 / 旅行 |
(晓辰 / 晓涵 等是 Azure 付费声音, edge-tts 没有, 别写。)

- **多角色**: `"voices": {"narrator": ..., "quote": ..., "host": ...}`, 场景写 `"role": "quote"` 就换声音;
  场景级 `"voice"` 优先级最高。引用人物原话、对话、"主持人插话"适合换声音, 一条片子 2 个声音足够, 别超过 3 个。
- **韵律**: `"prosody": "auto"` (默认) 按段落类型微调: 钩子 (第一场/hook) rate +6% pitch +2Hz, 转折 (以"然而/没想到/直到…"开头,
  或 `"beat": "twist"`) rate -6% pitch -2Hz, 结尾 (最后一场/quote) rate -5% pitch -1Hz。场景可直接写
  `"rate": "+20%"` `"pitch": "-3Hz"` `"volume": "+10%"` 覆盖; `"prosody": "off"` 全片匀速。
- **选声建议**: 科技/财经 → 云扬旁白 + 晓晓引用; 人物悲剧/商战 → 云健旁白 + 云希引用; 历史故事 → 云希 + 云健 (古人原话);
  美食/生活 → 晓伊 (+ 晓妮/晓北 方言彩蛋一场); 情感治愈 → 晓晓 / 晓雨; 儿童科普 → 云夏 + 晓伊。同样**不要每条都用云希**。

### BGM 情绪 `mood` (numpy 合成, 无版权)
`dark` 悲剧悬疑 · `epic` 逆袭商战 · `light` 科普搞笑 · `tech` 电子脉冲 · `warm` 温暖拨弦 · `calm` 舒缓铃音 (无鼓) · `upbeat` 轻快拍手。
同一标题每次合成一样; 不同标题会在调性 (±2~3 半音)、速度 (±8%)、琶音型上不同, 不用手动调。

## 场景版式 (type → params)
| type | 用途 | params |
|---|---|---|
| hook | 开场钩子: 数字滚动 + 标题逐字砸下 + 警示带 | number, prefix, suffix, title, tape |
| headline | 人物/事件登场: 巨大描边年份 + 标题 + 要点逐条 | kicker, title, lines[] |
| stamps | 连续打击/里程碑: 印章依次砸下 | title, stamps[] (≤3 个) |
| counter | 财富/人数涨跌: 数字从 from 滚到 to, 下跌自动变红 | label, from, to, prefix, suffix, note |
| versus | 对比: 两张卡左右滑入 + VS | left{name,value}, right{name,value} |
| quote | 金句收尾: 打字机 + 关注卡 | quote, endcard |
| image | 有本地图片时: 推拉镜头 + 说明 | src, caption |

题材专属画面 (行情牌、金币雨、K 线…) 写 `$P/custom.js`, 在 engine.js 之前加载:
```js
window.LAYOUTS_EXT = { coins: { build(el, p) { /* 建 DOM 一次 */ },
                                update(el, p, u, dur) { /* u=本场已过秒数, 只依赖 u 改样式 */ } } };
```
**铁律**: update 必须是 u 的纯函数 —— 不许 setTimeout / CSS transition / animation / Date.now / Math.random,
也不许在 update 里调 `R()` (原片门缝灯光闪烁就犯过: 同一帧每次渲染不一样)。随机数在 build 里取好存起来;
要"闪烁"用 `Math.sin(u*97)>0` 这类 u 的确定函数。
完整的专属画面写法见 `examples/livermore/original_engine.js`。

## 审核与合规
- 死亡/暴力不直写过程 ("一声枪响结束了传奇"), 无血腥、无武器特写
- 财经题材: 不荐股、不承诺收益, 结尾落在教训而非"你也能"
- 简介里加"本片由 AI 辅助制作" (平台要求)
- 真人名字可以讲公开史实, 不编造对话当真事, 不做人身攻击

## 常见坑
- 字幕分句: 每句 {t0, t1} 都要是 0-1 的比例 (原片 t1 存成累计字数 → 永远命中第一句, 整场字幕不动)
- 某句话出现的时刻: 用旁白里关键词的位置比例算 (`d * text.indexOf("一九一五") / text.length`), 别写死秒数 —— 改旁白后时长会变
- playwright 报 chromium 版本不对: 脚本已自动找 `~/.cache/ms-playwright/chromium-*`, 不用装
- 中文字体: 容器已有 Noto CJK / 文泉驿; 新字体别联网下
- 帧数大: 85s×24fps≈2000 张 png ≈1GB, 成片后可删 frames/
- 字幕在底部 220px 以上, 别把关键画面放进底部 300px (被抖音 UI 挡)
