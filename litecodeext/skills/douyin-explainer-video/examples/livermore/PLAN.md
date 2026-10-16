# PLAN: 天才交易员的陨落 — 竖版动漫科普视频

主题: 杰西·利弗莫尔 (Jesse Livermore) 1877-1940
规格: 1080x1920 竖版, ~90s, 24fps, 动漫/漫画风, edge-tts 旁白 + BGM
风格: 浮世绘+漫画分镜, 深海蓝 #0a1226 / 金 #f5c451 / 绯红 #e63946 / 米白

## Steps

- [ ] step1: script.py → script.json (7场台词) + edge-tts 逐场生成 mp3 + ffprobe 取时长 → timeline.json ✅
- [ ] step2: video.html + engine.js — seek(t) 纯函数时间轴渲染 (7个场景 + 字幕)
- [ ] step3: render.py — playwright 逐帧截图 frames/*.png (24fps), 后台跑
- [ ] step4: bgm.py — numpy 合成低音氛围 BGM → ffmpeg 合成 final.mp4 (帧序列+旁白+BGM)
- [ ] step5: 验证: ffprobe 时长/抽帧截图检查
- [ ] step6: cover.html → cover.png (1080x1440 漫画封面)
- [ ] step7: publish.md — 发布包 (标题x3 + 正文 + 话题标签)

## 事实核对 (旁白依据)
- 1877 生于马萨诸塞; 14岁离家去波士顿, 在对赌行(bucket shop)抄报价板
- 20岁本金约$10,000, 被新英格兰对赌行联合拉黑
- 1907 单日做空赚 $3M; 摩根(J.P. Morgan)托人请他手下留情
- 1908-1934 多次破产 (第4次1915前; 1934第4/5次正式破产, 负债$5M+)
- 1915 借$500东山再起
- 1929 大崩盘做空获利 ~$100M (史上个人单日最大获利之一)
- 1940-11-28 纽约 Sherry-Netherland 酒店 3202 房身亡, 遗书 "My life has been a failure" (我的人生是一次失败)
- 名言: "市场永远不会错, 观点常常会错"

## 审核安全
- 死亡不直写"开枪自杀", 用"一声枪响结束了传奇"; 无枪械画面
- 无荐股/收益承诺, 结尾是悲剧教训向
