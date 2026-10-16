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

## 流程 (7 步, 用 task_create 建好再逐个推进)

1. **选题 + 文案** → `$P/script.json`。先读 `copywriting.md` 写口播。
   事实先核对 (年份/数字/名言), 写进 PLAN.md 的"事实核对"段; 不确定的数字别写死。
2. **配音 + 时间轴**: `python3 $SK/scripts/tts.py` → `audio/*.mp3` + `timeline.json`
   (旁白里有 3 个以上连续英文字母会被拦: TTS 会念怪, 改中文)。
3. **画面**: `cp $SK/templates/video.html $SK/templates/engine.js $P/`,
   `python3 $SK/scripts/render.py snap` 出每场中点关键帧 → `python3 $SK/scripts/grid.py`
   → `vision_ocr preview/grid.png` 逐帧查: 文字溢出 / 遮挡 / 乱码 / 字幕可读。不过关就改, 再 snap。
4. **逐帧渲染** (耗时 5-15 分钟): `execute_shell background=true: python3 $SK/scripts/render.py all`。
   等待期间并行做 5/6/7, 别干等。
5. **BGM + 混音**: `python3 $SK/scripts/bgm.py [dark|epic|light]` → `python3 $SK/scripts/merge.py mix`;
   渲染完再 `python3 $SK/scripts/merge.py final` → `final.mp4`。
6. **封面** 1080x1440: 参照 `examples/livermore/cover.html` 写 `cover.html`,
   `python3 $SK/scripts/shot.py cover.html cover.png 1080 1440`, vision_ocr 打分 ≥8 才过。
7. **发布包** `publish.md`: 标题 x3 + 正文 + 话题 8-10 个 + 置顶评论 + 发布 checklist
   (模板见 `examples/livermore/publish.md`)。
   最后 ffprobe 核对时长/分辨率, 抽 3 帧 vision 复查, 汇报文件路径。

## script.json
```json
{
 "title": "天才交易员的陨落",
 "voice": "zh-CN-YunxiNeural", "rate": "+14%", "gap": 0.7,
 "mood": "dark",                       // BGM: dark 悲剧悬疑 / epic 逆袭商战 / light 科普搞笑
 "theme": {"bg": "#0a1226", "accent": "#f5c451", "alert": "#e63946"},
 "highlight": {"gold": ["一亿美元"], "red": ["破产"]},   // 字幕关键词上色
 "scenes": [
  {"id": "s0", "act": "序章 · 1929", "type": "hook",
   "params": {"number": 100000000, "prefix": "$", "title": "天才交易员的陨落", "tape": "BLACK THURSDAY"},
   "text": "旁白全文, 一场 8-16 秒 (约 40-80 字)"}
 ]
}
```
声音: 男声讲故事 `zh-CN-YunxiNeural`, 沉稳 `zh-CN-YunjianNeural`, 女声 `zh-CN-XiaoxiaoNeural`。

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
**铁律**: update 必须是 u 的纯函数 —— 不许 setTimeout / CSS transition / animation / Date.now / Math.random
(随机用 engine 的 `R()`, 在 build 里取好)。否则逐帧截图会抖、重渲不一致。
完整的专属画面写法见 `examples/livermore/original_engine.js`。

## 审核与合规
- 死亡/暴力不直写过程 ("一声枪响结束了传奇"), 无血腥、无武器特写
- 财经题材: 不荐股、不承诺收益, 结尾落在教训而非"你也能"
- 简介里加"本片由 AI 辅助制作" (平台要求)
- 真人名字可以讲公开史实, 不编造对话当真事, 不做人身攻击

## 常见坑
- playwright 报 chromium 版本不对: 脚本已自动找 `~/.cache/ms-playwright/chromium-*`, 不用装
- 中文字体: 容器已有 Noto CJK / 文泉驿; 新字体别联网下
- 帧数大: 85s×24fps≈2000 张 png ≈1GB, 成片后可删 frames/
- 字幕在底部 220px 以上, 别把关键画面放进底部 300px (被抖音 UI 挡)
