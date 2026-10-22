---
name: comfyui-video
description: >
  用任意 ComfyUI 服务生成视频: 拿到用户给的 ComfyUI 地址先 probe, 再选工作流出片。自带 MiniMax H3 四种模式模板
  (文生视频 t2v / 图生视频 i2v / 首尾帧 fl2v / 参考生视频 r2v, 带音轨), 也能用服务器已保存的其它视频工作流 (Wan/LTX 等)。
  素材自动上传, 线上机先等队列空, 下载 mp4 回传。触发: "生成视频" "图生视频" "首尾帧" "参考生视频" "文生视频" "comfyui 视频"。
aliases: [生成视频, 生视频, 图生视频, 文生视频, 首尾帧, 参考生视频, 参考视频生成, comfyui 视频, comfyui视频, minimax h3]
---

# ComfyUI 生视频 (通用: 给地址就能用)

**需要一个 ComfyUI 地址 (host:port)。用户没给时先问; 或用上次记住的地址 (`comfy.py endpoints`, 命令里省略地址 = 上次那个)。**
客户端和生图技能共用同一个脚本, 用法、跳板、线上机规矩、地址记忆全部相同 (细节见 comfyui-image 技能):

```bash
C="python3 ${LITECODE_HOME:-/opt/litecode}/skills/comfyui-image/scripts/comfy.py"   # 模板自动从 comfyui-image/ 与 comfyui-video/templates 找
```

## Keywords
生成视频 生视频 文生视频 图生视频 首尾帧 首帧 尾帧 参考生视频 参考图 参考视频 配音视频 带音轨 comfyui 视频工作流
minimax h3 t2v i2v fl2v r2v wan ltx 帧数 分辨率 运镜 镜头 短片 出片

## 流程
1. 地址 → `$C probe <host:port>` (内网要跳板: `$C --via-ssh <跳板别名> probe ...`, 成功后记住)。看三样:
   视频类节点 (特色节点里的 MiniMax H3 / Wan / LTX…)、视频模型 (unet_name 等)、已保存工作流。
2. 选工作流: 用户指定 > 服务器已保存且能转 API 的 (`$C workflows <地址> 名字.json` 先看节点和字段) > probe 里 ✓ 的 `h3_*` 模板。
   全 ✗ → 如实说缺什么节点/模型, 别硬套 (例如 Wan 机器上不能跑 h3 模板)。
3. `$C run <地址> -w <模板|文件|saved:名字> --prompt "..." [--frames N --width W --height H --seed S] [--input 槽位=文件或URL ...]`
4. 交付: `[out]` 行是本地 mp4 路径 (写进回复出预览卡片) + 服务器 `/view` 链接; `RESULT` 里 wall_s / queue_s / render_s 照实报耗时。
   建议 ffprobe 看一下分辨率/帧数/音轨, 或抽 2~3 帧给 vision 看内容对不对。

## MiniMax H3 四种模式 (模板 h3_*)

| 模板 | 模式 | 必需素材 (`--input`) | 可选素材 | 说明 |
|---|---|---|---|---|
| `h3_t2v` | 文生视频 | 无 | — | 纯文字 → 带环境音/对白的视频 |
| `h3_i2v` | 图生视频 | `first=首帧图` | — | 首帧固定, 按提示词动起来 |
| `h3_fl2v` | 首尾帧 | `first=首帧图` `last=尾帧图` | — | 从首帧自然过渡到尾帧 (同一个 fl2va 模型, 多接一个 last_frame) |
| `h3_r2v` | 参考生视频 | `ref_image=参考图` | `ref_image2` `ref_video` `ref_audio` | ref2va 模型; 提示词里用 `<Picture 1>` `<Picture 2>` `<Video 1>` `<Audio 1>` 指代素材; 没给的可选素材自动删节点 |

```bash
$C run <地址> -w h3_t2v  --prompt "清晨江南水乡, 一只橘猫走过石桥, 镜头跟随, 流水和鸟鸣" --frames 73
$C run <地址> -w h3_i2v  --input first=/tmp/litecode_workspace/p.png --prompt "人物抬头看向镜头, 雪花飘落"
$C run <地址> -w h3_fl2v --input first=a.png --input last=b.png --prompt "人物转身走向远处的火车"
$C run <地址> -w h3_r2v  --input ref_image=p.png --input ref_video=motion.mp4 \
   --prompt "<Picture 1> 中的男子按 <Video 1> 的动作走入画面、转身面向镜头"
```
- **素材**: 本地文件经 `POST /upload/image` 传到服务器 `input/<subfolder>/` (默认 litecode, `--subfolder` 可改; 图片/mp4/wav 都走这个接口),
  再把服务器自己的 `/view?filename=…&type=input` 链接填进 `LoadImageFromURL / LoadVideoFromURL / LoadAudioFromURL`。
  已经是 http(s) 链接就直接填 (必须是 **ComfyUI 服务器能访问到** 的地址)。参考视频 2~15 秒, 24fps。
- **硬约束** (违反会在采样深处才炸): 宽高是 **32 的倍数** (480/768/864/1344…, **720 不行**);
  帧数 `length` 必须 **17n+5** (22≈1s · 73≈3s · 124≈5s · 243≈10s), 固定 24fps。
- **默认参数**: 864×480 (16:9) · 124 帧 · **8 步** · res_multistep/simple/cfg 1 · seed 随机。竖屏用 `--width 480 --height 864`;
  高清 1344×768 (约 2.3 倍耗时)。测试/预览用 `--frames 73` 或 22。不要随便加步数 (线上正式片 15 步, 对比测试统一 8 步)。
- 多卡 (Raylight): 提交前 `ray_init` 自动**跟随服务器最近一次同类任务**的参数 (命名空间/卡数/注意力后端), 复用已加载的 Ray worker,
  不在线上机上另起一套占显存; 服务器没有这类历史才用模板值 (4 卡 Ulysses + TORCH_FLASH)。卡数不同 → `--set ray_init.GPU=2 --set ray_init.ulysses_degree=2`。
- 输出: `SaveVideo` 存 `output/litecode/h3_<模式>_xxxxx_.mp4` (`--prefix` 改前缀); h264 + aac 音轨。

## 其它视频工作流 (Wan / LTX / 用户自己的)
- `$C workflows <地址>` → `$C workflows <地址> xxx.json` 转成 API 格式并列出节点字段 → `$C run <地址> -w saved:xxx.json --prompt ... --set 节点.字段=值`。
- 快捷参数 `--frames` 会写到 length/num_frames/frames/video_length 字段, `--width/--height` 只改同时有宽高的节点; 写错节点就改用 `--set`。
- 输出节点是 VHS_VideoCombine 等也能取回 (history 里 gifs/videos/images 都会下载)。

## 线上机规矩 (重要, 脚本已内置)
- 视频机常有真实用户: 提交前**等 `/queue` 为空** (默认); 一次只提交一条; **绝不调用 `/free` `/interrupt`** (会让线上用户重新加载模型)。
- 同参数同种子重跑会命中缓存 (3 秒"完成"); 脚本检测到会警告, 换 `--seed`。
- 超时 (`--timeout`, 默认 1800s) 只打印 prompt_id, 不取消; 稍后 `$C wait <地址> <prompt_id>`。
- 和配音/字幕/剪辑合成的关系: 生成镜头用本技能, 拼接成片、配音、BGM 用 video-studio (timeline.json 的 visual 填生成的 mp4)。
- 已验证环境与实测耗时: `references/verified_envs.md` (例子, 不是默认值)。
