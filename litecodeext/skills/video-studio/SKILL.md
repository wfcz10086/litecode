---
name: video-studio
description: >
  配音与视频合成: 文字转语音 (多音色男/女/方言/粤语, 情绪语速音调可调, 输出 mp3/wav + 逐句时间戳/SRT)、
  文案 → 配音 → 字幕 (SRT + 烧录) → 图片/视频素材画面 → BGM (自动避让人声) → 成片 mp4, 多轨时间线严格对齐并自动校验。
  触发: "配音" "合成语音" "文字转语音" "TTS" "生成字幕" "合成视频" "做成片" "图片配音做成视频" "改写后重新配音"。
  做带动画模板的解说短视频走 douyin-explainer-video; 抖音链接解析/文案解读走 douyin-toolkit。
  要 AI 生成视频镜头 (文生/图生/首尾帧/参考生视频) 走 comfyui-video, 生成的 mp4 可直接当 timeline 的 visual。
aliases: ["合成视频", "做成片", "烧录字幕", "音画同步", "时间轨道", "图片配音做成视频"]
---

# 配音 + 成片合成 (时间线对齐)

```
VS=/opt/litecode/skills/video-studio/scripts
P=/tmp/litecode_workspace/<项目名>          # 素材、timeline.json、成片都放这里
```
成片/音频/字幕路径**写进回复** (出预览卡片)。

## Keywords
配音 语音合成 文字转语音 tts 朗读 旁白 男声 女声 音色 语速 情绪 字幕 srt 烧录字幕 合成视频 成片 剪辑 图片转视频
时间线 时间轴 音画同步 对齐 bgm 混音 ducking 改写文案 二创 口播视频

## 1. 只要配音
```bash
python3 $VS/tts.py --list                                    # 声音目录 (云希/云健/云扬/云夏/晓晓/晓伊 + 东北/陕西/粤语/台湾腔)
python3 $VS/tts.py --file $P/文案.txt -v 晓晓 --emotion 温柔 -o $P/voice.mp3 --srt $P/voice.srt
python3 $VS/tts.py --text "一句话" -v 云健 --rate +10% --pitch +3Hz -o $P/a.wav
```
- 引擎 edge-tts (微软在线神经语音, 免 key, 缺库自动 pip 装)。情绪预设: 平静/激昂/温柔/悲伤/欢快/严肃/悬疑
  (用语速/音调/音量近似, edge-tts 不支持真正的情感风格标签)。
- 同名 `.json` 里是每句真实人声起止时间 (silencedetect 量出, 去掉首尾静音), SRT 用的就是它。
- **声音克隆暂不支持** (需模型或云接口), 用户要克隆就说明方案: ① 阿里百炼 CosyVoice 声音复刻 (云端, 理论上可复用现有 DashScope key, 未实测; 需 10~20 秒样音并确认本人授权);
  ② 自建 CosyVoice2-0.5B / GPT-SoVITS (需 GPU, 约 2~5GB 模型, 可部署到 5090 机器)。克隆他人声音必须本人授权。

## 2. 成片: timeline.json → mp4
```bash
python3 $VS/timeline.py --example > $P/timeline.json       # 改成自己的
python3 $VS/timeline.py $P/timeline.json -o $P/final.mp4    # 30~90 秒片子约 0.5~2 分钟; 长片 background=true
```
```json
{
 "size": [1080, 1920], "fps": 30, "sample_rate": 48000,
 "voice": "云希", "emotion": "", "rate": "", "gap": 0.35,
 "subtitle": {"burn": true, "size": 66, "max_chars": 14, "margin_v": 300},
 "bgm": {"mood": "upbeat", "volume": 0.22, "duck": true, "fade_in": 1.0, "fade_out": 2.0},
 "segments": [
   {"text": "这一段的口播", "visual": "frames/f01.jpg", "motion": "zoom_in"},
   {"text": "下一段", "visual": "clip.mp4", "clip_start": 3, "voice": "晓晓", "emotion": "欢快"},
   {"text": "", "visual": "color:#1e293b", "min_dur": 1.5},
   {"text": "预录音频的字幕文本", "audio": "my_voice.wav", "visual": "b.png"}
 ]
}
```
- `visual`: 图片 (自动铺满裁切 + motion: zoom_in/zoom_out/pan/none) / 视频 (循环或截断到本段长度, 去原声, clip_start 起点) / `color:#hex` / 不写=轮换底色。
  相对路径相对 timeline.json 所在目录。
- `bgm`: `{"mood": ...}` 现场程序生成 (见 bgm-music, 无版权); `{"file": "x.mp3"}` 用现成曲子 (自动循环/裁剪); `null` 不要 BGM。
- 一段一个画面; 一段可以多句, 字幕按句自动切屏 (每屏 ≤ max_chars 字)。

### 时间线对齐 (脚本硬保证, 不用手算)
- 段时长 = 配音实测时长 + gap, **取整到整帧**; 配音按样本数补齐 → 画面切换、配音、字幕同一套帧边界, 不累积漂移。
- 字幕起止 = 每句真实人声起止 (不是按字数估); 30fps CFR; 音频统一 48kHz; BGM 裁到成片总长, 结尾淡出正好落在片尾, 人声出现时压低 (sidechain ducking)。
- 渲染完自动校验并写 `final.report.md/json`: 固定帧率 / 帧数=各段之和 / 音画时长差 ≤ 1 帧 / 末条字幕 ≤ 成片时长 /
  字幕与人声轨实测起止误差 ≤ 100ms / 段起点在整帧。**任一 FAIL 退出码 1, 不许交付**; 音画差超标会自动重切音频一次再校验。
- 交付时把报告里的结论 (总长、帧数、各项 OK) 简要贴给用户; 再抽 2~3 帧 vision_ocr 看字幕有没有出屏/遮挡。

## 3. 「解读抖音 → 改写 → 新配音视频」一条龙
1. douyin-toolkit `analyze.py` 拿口播转写 + 关键帧 (`analysis/frames/*.jpg`), vision_ocr 读字幕, 输出解读。
2. 按解读里的「可复用文案模板」改写新文案 (原创表达, 别照抄原句), 拆成 4~8 段, 每段 1~2 句。
3. 写 timeline.json: 画面用关键帧图 (或用户素材/生图), bgm 选 mood; 跑 timeline.py; 校验通过后交付 mp4 + srt + 报告。
- 原视频画面属于原作者, 二创发布前提醒用户授权问题; 能用自有素材/生图就别直接搬原画面。
