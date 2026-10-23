---
name: bgm-music
description: >
  配乐 / 背景音乐: 本地程序化生成无版权 BGM (情绪/BPM/调性/时长, 无缝循环, 淡入淡出, mp3/wav)、
  检索下载 CC 授权可商用音乐 (ccmixter / archive.org, 自动记录授权与署名)、带人声 / 带歌词的 AI 歌曲走「能力接入」里的音乐卡 (如自建 ACE-Step、MiniMax)。
  触发: "配乐" "背景音乐" "bgm" "纯音乐" "免版权音乐" "无版权音乐" "循环音乐"。**要人声 / 歌词的歌一律用 ai-song 技能。**
  给成片加 BGM 直接在 video-studio 的 timeline.json 里写 bgm 字段即可。
aliases: ["配乐", "背景音乐", "bgm", "免版权音乐", "无版权音乐", "纯音乐", "垫乐"]
---

# 配乐 / BGM

```
BM=/opt/litecode/skills/bgm-music/scripts
OUT=/tmp/litecode_workspace/music        # 产出目录, 路径写进回复 (出播放/下载卡片)
```

## Keywords
配乐 背景音乐 bgm 音乐 做首歌 作曲 生成音乐 纯音乐 免版权 无版权 可商用 版权音乐 循环 loop 淡入 淡出 节奏 bpm 调性
cc授权 署名 ccmixter archive minimax 歌词 歌曲

## 怎么选
| 需求 | 用法 | 版权 |
|---|---|---|
| 短视频垫乐, 要快、要稳、要能商用 | **bgm_gen.py 本地生成** (默认) | 本地程序生成, 无第三方版权 |
| 想要真人演奏质感的成品曲 | music_search.py 搜 CC 曲 | 按曲目协议 (默认只给可商用的 BY/BY-SA/CC0, 需署名) |
| **要带人声 / 带歌词的歌** | **用 ai-song 技能** (固定流程: 写歌词 → 能力接入里的 AI 作曲卡 → 核对唱词) | 以模型协议为准, 发布标注 AI 生成 |

> 用户要「歌词 / 人声 / 唱」时**别用本地生成交差**: 本地只能出纯音乐。有音乐卡就用卡, 都没有再如实说明并给出纯音乐 + 歌词文本。

## 1. 本地生成 (不联网, 秒级)
```bash
python3 $BM/bgm_gen.py --list                                   # 8 种情绪预设
python3 $BM/bgm_gen.py --mood upbeat --duration 45 -o $OUT/bgm_upbeat.mp3
python3 $BM/bgm_gen.py --mood calm --bpm 68 --key D --duration 32 --loop -o $OUT/calm_loop.wav   # 无缝循环段
```
- mood: calm 舒缓 / upbeat 轻快 / epic 史诗 / sad 伤感 / tech 科技 / warm 温暖 / funny 搞笑 / suspense 悬疑。
- 非 loop: 输出正好 duration 秒, 带淡入 (--fade-in 默认 1s) 淡出 (--fade-out 默认 2.5s); loop: 按整小节取整, 尾音折回开头, 首尾无缝, 不加淡入淡出。
- mp3 自动响度归一到约 -16 LUFS; `--seed` 换一个就换琶音型/细节, 同参数同 seed 结果完全一致。
- 质感是合成器 pad + 琶音 + 鼓机的简单配乐, 够垫底, 别当成品歌曲宣传。

## 2. CC 授权音乐检索下载
```bash
python3 $BM/music_search.py search --tags piano,ambient,instrumental --min 60 --max 240 --limit 8
python3 $BM/music_search.py search --tags electronic --src archive
python3 $BM/music_search.py download ccmixter:2905 -o $OUT
```
- 标签用英文: ambient piano electronic cinematic chill happy guitar lofi instrumental …; 要纯音乐加 `instrumental`。
- 下载后旁边有 `<曲名>.LICENSE.txt` (协议、来源页、**署名文案**), 交付时把署名文案告诉用户, 发布时贴进简介。
- 默认过滤掉 NC (禁止商用); 用户明确非商用才加 `--nc`。Pixabay Music 在本机网络 403, 未接入。

## 3. 外部 AI 作曲 (可选)
```bash
python3 $BM/music_api.py status
python3 $BM/music_api.py gen --prompt "轻快电子流行, 美食探店, 120bpm, 纯音乐" -o $OUT/ai.mp3
python3 $BM/music_api.py gen --prompt "民谣, 温暖" --lyrics $OUT/歌词.txt -o $OUT/song.mp3
```
- 需 env `MINIMAX_API_KEY` 或 config.json `music.minimax_api_key`; 没配时 status/gen 只返回方案说明, **不报错**,
  这时改用第 1 步本地生成, 并告诉用户配置 key 后可用 AI 作曲。

## 4. 给视频配 BGM
在 video-studio 的 timeline.json 写 `"bgm": {"mood": "warm", "volume": 0.22, "duck": true}` 或 `{"file": "$OUT/xx.mp3"}`:
自动裁剪/循环到成片长度、首尾淡入淡出、人声出现时压低 (ducking)。单独给现成视频换/加配乐:
```bash
ffmpeg -i in.mp4 -stream_loop -1 -i bgm.mp3 -filter_complex \
 "[1:a]volume=0.25,afade=t=in:d=1[b];[0:a]asplit[v][sc];[b][sc]sidechaincompress=threshold=0.02:ratio=6[bd];[v][bd]amix=inputs=2:normalize=0:duration=first[a]" \
 -map 0:v -map "[a]" -c:v copy -c:a aac -shortest out.mp4
```
(原视频没音轨时去掉 sidechain, 直接 `-map 0:v -map 1:a -shortest`, 末尾淡出按视频时长算 `afade=t=out:st=<时长-2>:d=2`。)
