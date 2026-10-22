---
name: comfyui-image
description: >
  用任意 ComfyUI 服务生图 / 改图: 拿到用户给的 ComfyUI 地址 (host:port) 先 probe 探索显卡、模型、已保存工作流,
  再选工作流 (服务器已保存的优先, 否则用自带 Qwen-Image-2.1 文生图 / 图片编辑 / 多图融合、通用 Checkpoint 模板,
  按探到的模型名自动适配), 提交、等结果、下载图片回传。支持 SSH 跳板访问内网地址, 线上机先等队列空。
  触发: "生图" "文生图" "画一张/画一只" "出图" "改图" "图片编辑" "comfyui" "qwen-image"。生视频走 comfyui-video。
aliases: [生图, 文生图, 画图, 出图, 改图, 画一只, 画一张, 画个, qwen-image, qwen image, comfyui, comfyui 生图, 图片编辑, 编辑图片]
---

# ComfyUI 生图 (通用: 给地址就能用)

**需要一个 ComfyUI 地址 (host:port)。用户没给时先问; 或用上次记住的地址 (`comfy.py endpoints` 查看, 命令里省略地址就自动用上次那个)。**
地址、跳板、模型名都不是写死的 —— 每次以 probe 结果为准。

```bash
C="python3 ${LITECODE_HOME:-/opt/litecode}/skills/comfyui-image/scripts/comfy.py"   # 只用标准库; 产物默认在 $WORKSPACE/comfy_outputs/<日期>/
```

## Keywords
生图 文生图 画图 出图 改图 图片编辑 换背景 多图融合 合影 海报文字 comfyui comfy 工作流 workflow qwen-image qwen image
stable diffusion sdxl checkpoint 模型 显卡 出一张 画一张 画一只 生成图片 AI绘画 ai画图

## 流程 (按顺序, 别跳)

1. **地址**: 用户给了 `host:port` 就用; 没给 → `$C endpoints` 看记住的, 只有一个/用户说"再来一张"就用它, 否则问用户。
2. **探索** `$C probe <host:port>` (结果缓存 6 小时, `--refresh` 强制重拉): 版本、显卡显存、队列、
   各加载节点的可用模型 (unet/ckpt/clip/vae/lora)、特色节点、服务器已保存工作流、技能模板能否在这台跑 (✓/✗ + 原因)。
   - 本机连不上 (内网地址) → 报错会提示要跳板: 问用户或查记忆有没有能访问它的 SSH 主机别名, 然后
     `$C --via-ssh <跳板> probe <host:port>`; 成功后跳板记进 `comfyui_endpoints.json`, 以后自动用。
     跳板写法: `~/.ssh/config` 里的 Host 别名, 或直接 `user@host:port` (密钥登录)。**ssh_list_hosts 列出的 LiteCode 主机库别名
     (系统 ssh 不认识) 要换成它显示的 `user@host:port` 写法**; 只存了密码的主机做不了转发, 可先 `ssh_exec` 到那台机器上 curl 验证地址是否可达。
   - 探完把地址、跳板、能用的模板用 save_memory 记一句 (例: "ComfyUI 10.x.x.x:8488 经跳板 jump1, Qwen-Image-2.1 可用")。
3. **选工作流** (优先级从高到低):
   - 用户指定的工作流文件 / 服务器上的工作流名 → `-w 文件.json` 或 `-w saved:名字.json`
     (`$C workflows <地址>` 列出, `$C workflows <地址> 名字.json` 下载并转成 API 格式看节点; 含子图的网页工作流转不了, 换模板)。
   - probe 里 ✓ 的技能模板: `qwen21_t2i` 文生图 · `qwen21_edit` 图片编辑 · `qwen21_multi` 两图融合 · `sd_checkpoint_t2i` 通用 checkpoint。
   - **全是 ✗** → 如实告诉用户缺什么 (缺哪些节点/模型, 这台有哪些模型), 问他有没有对应工作流 (网页里"导出 (API)"的 json), 别硬套。
4. **出图** `$C run <地址> -w <模板|文件|saved:名字> --prompt "..." [参数]`, 例:
   ```bash
   $C run 10.0.0.5:8488 -w qwen21_t2i --prompt '一只橘猫坐在窗台上, 招牌写着"LiteCode"' --width 1376 --height 768
   $C run 10.0.0.5:8488 -w qwen21_edit --input image=/tmp/litecode_workspace/a.png --prompt "把背景换成日落海滩"
   $C run -w qwen21_multi --input image=a.png --input image2=https://x/b.png --prompt "让图1的人穿上图2的衣服"   # 省略地址=上次的
   ```
   - 快捷参数: `--prompt --negative --seed --steps --cfg --width --height --sampler --scheduler --denoise` (模板知道写哪个节点; 自己的工作流靠启发式, 找不到会报错让你改用 `--set`)。
   - 任意字段: `--set 节点id.字段=值` (节点 id / 标题 / 唯一的类型名都行), 删节点 `--drop 节点`。
   - 素材: `--input 槽位=本地文件或URL` —— 本地文件自动 `POST /upload/image` 上传 (子目录 `--subfolder`, 默认 litecode);
     URL 槽 (`LoadImageFromURL` 等) 填服务器自己能访问的 `/view` 链接, 文件槽 (`LoadImage`) 填上传后的文件名。
   - 不给 `--seed` 就随机 (同参数同种子会命中服务器缓存, 秒出旧图; 脚本会警告)。`--dry-run` 只生成最终工作流 json 不提交。
   - 模型名: 模板里的模型这台没有时, 只换成**名字相近**的同系列文件 (如 bf16↔int8); 不相近就报"缺模型 X (这台有 …)", 照实告诉用户。
5. **交付**: 输出里 `[out] <本地路径>  <服务器 /view 链接>`, 最后一行 `RESULT {json}` (prompt_id、wall_s 总耗时、queue_s 排队+加载、
   render_s 服务器执行、files)。**把本地图片路径写进回复** (网页会出预览卡片), 附上耗时; 改图类任务建议 vision 看一眼结果是否符合要求。

## 线上机规矩 (脚本已内置)
- 提交前默认等 `/queue` 为空 (有人在用就每 30 秒报一次进度; 等满 `--idle-timeout` 默认 30 分钟仍不空就不提交, 退出码 3)。
  确认是独占机才 `--no-wait-idle`, 或 `$C remember <地址> --wait-idle no` 记住。
- **永不调用 `/free` `/interrupt`**, 也不删别人的队列; `--timeout` 到了只打印 prompt_id, 之后 `$C wait <地址> <prompt_id>` 接着取。
- 不要同时提交多张 (服务器单卡串行, 只会互相排队); 要多张就跑多次 run, 或改 `latent.batch_size`。

## Qwen-Image-2.1 参数 (模板默认已是提速档)
- 默认 steps=15 + dpmpp_2m + simple + cfg 1.0 (实测与 25 步 euler 画质相当, 快约 40%); 10 步会崩, 别再降。要更细 `--steps 25`。
- 尺寸 16 的倍数。官方画幅 (2K 原生; 1K 预览宽高各减半): 1:1 2048² · 4:3 2400×1792 · 3:4 1792×2400 · 3:2 2528×1696 ·
  2:3 1696×2528 · 16:9 2752×1536 · 9:16 1536×2752。1K 出图最快, 用户要高清再上 2K。
- 图里要出现的文字用**双引号**括起来; 中英文都能渲染。编辑/融合的输出尺寸跟随图1 比例, 像素预算 `--set enc.resolution=2048` (≈4MP)。
- 多图融合提示词用 "图1/图2" 指代; 最多 10 张参考 (自己加 LoadImage 节点 + `enc.images.image_N`)。

## 其它通道 / 关系
- 用户给的地址不是 ComfyUI (probe 报 `/system_stats` 404 之类), 而是某个生图平台的 JSON 接口 → 按它的文档用 curl 调; 已知平台的调用方式见 `references/verified_envs.md`。
- `image_gen` 工具: 读 config.json `imagegen` 段的固定 provider, 适合"配好一个默认生图后端"; 用户给了 ComfyUI 地址 / 要探索模型 / 要改图时用本技能。
- 只要排版类海报、不需要 AI 生成画面 → canvas-design。生视频 → comfyui-video (同一个 comfy.py)。
- 已验证过的环境与实测耗时见 `references/verified_envs.md` (只是例子, 不是默认值)。

## 排错
- `[err] 本机连不上 …` → 地址/端口错, 或需要 `--via-ssh <跳板>`; `ssh` 报 Bad owner (容器里以 root 跑) 脚本已自动绕开。
- `工作流校验失败` → 看 node_errors 里哪个节点哪个字段; 常见是模型名、尺寸不合法、素材文件名不存在。
- `缺节点 X` → 这台没装对应自定义节点, 换模板或让用户提供该机器能跑的工作流。
- 执行失败 `[err] 执行失败: {...exception_message}` → 原样告诉用户; 显存不够就降分辨率。
- 转发是后台常驻的 `ssh -N -L 127.0.0.1:<端口>:…` 进程, 端口按 (跳板, 目标) 固定, 下次直接复用; 不需要时 `pkill -f "ssh .*-N .*-L 127.0.0.1"`。
