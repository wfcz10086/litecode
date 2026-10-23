# 自建 GPU 生成服务：部署与二次部署

[返回 README](../README.md) · 部署文件在 [`deploy/gpu-services/`](../deploy/gpu-services/)

LiteCode 的配音、写歌、语音识别、动漫视频、MV，用的都是自己显卡上的开源服务。这页写清楚怎么在一台新的 GPU 机器上把它们装起来、对外开放、接进 LiteCode。

```
 LiteCode (任意电脑 / 桌面版 exe)
      │  https://你的域名/svc/<口令>/{asr,music,tts,anime,image,h3}/…
      ▼
 中转机 (有公网 IP + nginx + HTTPS)
      │  nginx 校验口令 + 限流 → 127.0.0.1:47011~47016
      │  systemd 守护的 ssh -L 端口转发 (断线自动重连)
      ▼
 GPU 机器 (可以在内网, 不用开任何公网端口)
      18011 SenseVoice · 18012 ACE-Step · 18013 IndexTTS · 18014 AniSora · 8488 Qwen-Image · 8188 MiniMax H3
```

## 有哪些服务

| 服务 | 用途 | 镜像 | 显存 | 许可证 |
|---|---|---|---|---|
| SenseVoice | 语音识别（中 / 粤 / 英 / 日 / 韩） | `litecode-svc-sensevoice` | ~1.2G | 代码 MIT，权重 FunASR Model License（保留署名可商用） |
| ACE-Step 1.5 XL | 写歌（带人声 + 歌词） | `litecode-svc-acestep` | ~17G | MIT |
| IndexTTS 2.5 | 配音、情绪、声音克隆 | `litecode-svc-indextts2` | ~7G | bilibili 模型许可（可免费商用，超大规模需另申请；不是 OSI 开源许可） |
| AniSora V3.2 | 动漫图生视频 | `litecode-svc-anisora` | 5 卡，每卡峰值 ~21G | Apache-2.0 |
| Qwen-Image 2.1 | 生图（ComfyUI） | 已有镜像，另行部署 | ~16G | Apache-2.0 |
| MiniMax H3 | 视频，含参考图 + 参考音频（ComfyUI） | 已有镜像，另行部署 | 4 卡 | 以模型发布方为准 |

模型权重都已打进镜像，拉下来就能跑；四个 `litecode-svc-*` 共用约 18G 的底层。

## 第 1 步：GPU 机器

前提：NVIDIA 驱动、`nvidia-container-toolkit`、Docker + docker compose，磁盘留 250G。

```bash
cd deploy/gpu-services
cp .env.example .env            # 改显卡分配 (见 .env 里的说明)
docker login ccr.ccs.tencentyun.com   # 镜像在私有仓库时
./svc.sh pull                   # 拉全部镜像
./svc.sh up                     # 启动
./svc.sh status                 # 每个服务: 端口 / 显卡 / 显存 / 健康
./svc.sh test                   # 冒烟测试
```

**显卡绑定（重要）**：compose 里每个服务同时写了 `device_ids`、`NVIDIA_VISIBLE_DEVICES` 和 `/dev/nvidiaN` 设备文件。
- **`/dev/nvidiaN` 的 N 不等于 nvidia-smi 卡号**，用 `grep -H "Device Minor" /proc/driver/nvidia/gpus/*/information` 查对应关系。
- 显式挂设备文件，是为了防止宿主机执行 `systemctl daemon-reload` 后容器丢 GPU（报 `Failed to initialize NVML: Unknown Error`）。
- 已在跑的 GPU 机器上**不要执行 `systemctl daemon-reload`**。

从源码重建镜像、各服务的接口与 curl 示例，见 [`deploy/gpu-services/README.md`](../deploy/gpu-services/README.md)。

## 第 2 步：中转机（对外开放）

1. **SSH 端口转发**：中转机要能 `ssh <gpu机器>`（密钥登录）。把 [`relay/litecode-gpua-svc.service`](../deploy/gpu-services/relay/litecode-gpua-svc.service) 里最后的 `gpu-a` 改成你的 ssh 别名，然后：
   ```bash
   sudo cp litecode-gpua-svc.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now litecode-gpua-svc
   curl -s http://127.0.0.1:47012/health      # 能返回就通了
   ```
   - 只监听 127.0.0.1，不对外。
   - 不要从 GPU 机器反向 `ssh -R`：中转机的 sshd 若是 `GatewayPorts yes`，端口会被强制绑到公网，没有鉴权的服务就直接暴露了。
2. **nginx 口令入口**：把 [`relay/nginx-svc.conf`](../deploy/gpu-services/relay/nginx-svc.conf) 放进 HTTPS 的 server 块，`<口令>` 换成随机串，http 块加上 `limit_req_zone`，然后 `sudo nginx -t && sudo systemctl reload nginx`。
   ```bash
   curl -s https://你的域名/svc/<口令>/music/health     # 200 = 通
   curl -s https://你的域名/svc/错的/music/health       # 404 = 口令生效
   ```
3. **换口令**：改 nginx 里的 `<口令>` 并 reload，旧口令立刻失效；已经发出去的定制安装包要重新打。

## 第 3 步：接进 LiteCode

左上角「🔌 能力接入」→「新增」，每个服务一张卡片：标题 + 一段明文（地址 + curl）。

| 卡片 | 类型 | 地址 | 备注 |
|---|---|---|---|
| ACE-Step 音乐 | 音乐 | `https://你的域名/svc/<口令>/music` | ai-song 技能用 |
| SenseVoice 语音识别 | 语音识别 | `https://你的域名/svc/<口令>/asr/v1` | OpenAI 兼容 |
| IndexTTS 配音 | 语音合成 | `https://你的域名/svc/<口令>/tts/v1` | OpenAI 兼容 `/audio/speech` |
| AniSora 动漫视频 | 生视频 | `https://你的域名/svc/<口令>/anime` | 只支持图生视频 |
| Qwen-Image 生图 | 生图（ComfyUI） | `https://你的域名/svc/<口令>/image` | **高级里 extra 加 `"self_url": "http://127.0.0.1:8188"`** |
| MiniMax H3 视频 | 生视频（ComfyUI） | `https://你的域名/svc/<口令>/h3` | 同上，`self_url` 必须填 |

`self_url` 是 ComfyUI 服务器访问它自己的地址。参考图、参考音频是以链接的形式交给服务器的，经代理访问时如果不填，服务器会去访问代理地址，报 `Connection refused`。

每张卡点「测试」，都通过后就能用，比如：
- “写一首古风歌”（ai-song）
- “做个动漫 MV”（anime-short）
- “给这段话配音”（text_to_speech 自动用默认配音卡）

## 第 4 步：打带预置配置的安装包（可选）

想把模型和这些卡片预先配好、发给别人直接装：

```bash
LITECODE_DIST_DIR=/某个下载目录 \
LITECODE_PRESET_CONFIG=preset-config.json \
LITECODE_INSTALLER_NAME=mycompany-litecode-Setup \
LITECODE_NO_PUSH=1 \
bash packaging/release.sh <版本号> --full --skip-tests
```

- `preset-config.json` 可以带这几段：`model`、`models`、`default_model_id`、`vision_fallback_id`、`vision`、`providers`、`search`、`tts`、`kb`。
- 安装时自动合并进配置，不再询问模型；登录密码、server token 仍在本机生成。
- 卡片地址要用公网口令地址，不能用 `127.0.0.1`。
- ⚠️ 预置配置里有各家 key 和口令，这种安装包**不要公开发布**。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 容器里 `nvidia-smi` 报 NVML Unknown Error | 宿主机执行过 `systemctl daemon-reload`；用 compose 重建该容器（`./svc.sh up <服务>`），compose 已显式挂设备文件 |
| H3 参考音频 / 参考图报 Connection refused | 卡片没填 `self_url` |
| 第一次调用很慢 | 冷启动：ACE-Step 首个请求约 60 秒，AniSora 容器启动约 100 秒 |
| AniSora 一直排队 | 同时只跑一个任务，每个约 5 分钟 |
| 公网地址 404 | 口令写错，或 nginx 没 reload |
