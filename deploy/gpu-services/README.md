# litecode-svc —— 开源 AI 能力服务包 (语音识别 / 音乐 / 配音 / 动漫视频)

四个独立的 GPU 服务，每个都是一个**自带模型权重**的 Docker 镜像，用一份 docker-compose 统一编排、一个 `svc.sh` 管理。
给 LiteCode 之类的 Agent 当 HTTP 能力接口调用，也可以单独使用。

| 服务 | 能力 | 模型 | 默认端口 | 显存 | 镜像大小 | 许可证 |
|---|---|---|---|---|---|---|
| sensevoice | 语音识别 (中/粤/英/日/韩 + 情绪标签)，OpenAI 兼容 | SenseVoiceSmall + fsmn-vad | 18011 | ~1.2 GB | 20 GB | 代码 MIT，权重 FunASR Model License |
| acestep | 带人声歌词的歌曲生成 | ACE-Step 1.5 XL-turbo (4B DiT) + 1.7B LM | 18012 | ~17 GB | 56 GB | MIT |
| indextts2 | 语音合成 / 声音克隆 / 情绪 / 语速，OpenAI 兼容 | IndexTTS-2.5 | 18013 | ~7 GB | 39 GB | bilibili Model Use License |
| anisora | 动漫图生视频 (5 秒，16fps) | Index-AniSora V3.2 (Wan2.2 i2v-A14B，bf16) | 18014 | 5 卡各 ~21 GB | 88 GB | Apache-2.0 |

镜像: `${REGISTRY}/litecode-svc-<服务>:${TAG}`，默认 `ccr.ccs.tencentyun.com/preview/litecode-svc-<服务>:v1`。
镜像大小里约 18 GB 是四个镜像共用的底层 (vllm-openai:v0.25.1)，拉一次即可。

---

## 一、新机器从零部署

### 1. 前提
- Linux x86_64，NVIDIA 驱动 ≥ 570 (CUDA 12.8/13.0 运行时)，`nvidia-smi` 正常
- Docker Engine + docker compose v2 + nvidia-container-toolkit (`docker run --rm --gpus all ubuntu nvidia-smi` 能看到卡)
- 显存: sensevoice+indextts2 一张 24GB 卡；acestep 一张 24GB 卡 (≥20GB)；anisora 5 张 24GB 卡 (或 4/8 张，见下)
- 内存: anisora 每个进程常驻约 11GB (T5 放 CPU)，加载时 rank0 额外 ~29GB；建议 ≥128GB
- 磁盘: 全部镜像约 160 GB (含共用底层)

### 2. 拿到本目录并登录镜像仓库
```bash
git clone <本仓库> litecode-svc && cd litecode-svc
docker login ccr.ccs.tencentyun.com          # 腾讯云个人仓库账号 (私有仓库才需要)
cp .env.example .env                        # 按下面第 3 步改显卡和端口
```

### 3. 分配显卡 (必做)
`.env` 里每个服务要填两样: **卡号** (`*_GPU`，nvidia-smi 的 index) 和该卡的**设备文件** (`*_DEV`，`/dev/nvidiaN`)。

> **/dev/nvidiaN 的 N 不等于卡号**。按下面查，`Device Minor` 就是 N:
> ```bash
> nvidia-smi --query-gpu=index,pci.bus_id --format=csv
> for f in /proc/driver/nvidia/gpus/*/information; do echo "$(dirname $f | xargs basename)  $(grep 'Device Minor' $f)"; done
> ```
> 用 PCI 总线号把两边对上。例: 在 gpu-a 上 卡0-3=nvidia6-9、卡4=nvidia10、卡5=nvidia11、卡6=nvidia4、卡7=nvidia5、卡8-11=nvidia0-3。

为什么要同时写设备文件: 只靠 `--gpus`/`device_ids` 时，宿主机一执行 `systemctl daemon-reload`
(装 systemd 服务时常见)，cgroup v2 下容器就会丢 GPU (`Failed to initialize NVML: Unknown Error`)。
compose 里已同时写 `deploy.device_ids` + `NVIDIA_VISIBLE_DEVICES` + 显式挂 `/dev/nvidiactl`、`/dev/nvidia-uvm(-tools)`、`/dev/nvidiaN`。

AniSora 默认 5 卡 (Ulysses 序列并行度须整除 40 个注意力头，可选 1/2/4/5/8；24GB 卡至少 5 张)。
改卡数要同时改 `.env` 的 `ANISORA_NPROC` 和 docker-compose.yml 里 anisora 的 `device_ids`/`devices` 条目数。

### 4. 拉镜像并启动
```bash
./svc.sh pull            # 或 ./svc.sh pull acestep 只拉一个
./svc.sh up              # 或 ./svc.sh up acestep
./svc.sh status          # 状态 / 健康 / 镜像大小 / 端口 / 显卡 / 显存
./svc.sh test            # 冒烟测试
```
启动后核对每个容器只看到自己的卡:
```bash
docker exec lc-acestep nvidia-smi --query-gpu=index,pci.bus_id --format=csv
```

### 5. 冷启动耗时 (实测, RTX 4500 Ada 24GB)
| 服务 | 容器启动到可用 | 首个请求 |
|---|---|---|
| sensevoice | ~15 s | 0.3 s |
| acestep | ~60 s (启动即加载模型) | 正常 |
| indextts2 | ~60 s | 首次 ~7 s，之后 2~3 s |
| anisora | ~100 s (5 卡加载 + FSDP 广播) | ~290 s/条 |

---

## 二、接口与 curl 示例
下面 `$HOST` 换成部署机地址，例如 `HOST=http://127.0.0.1`。

### sensevoice :18011 —— 语音识别
```bash
curl -s $HOST:18011/v1/audio/transcriptions -F file=@speech.wav -F language=zh -F response_format=json
# → {"text":"……"}   response_format: json|text|verbose_json; language: auto|zh|en|yue|ja|ko
curl -s $HOST:18011/api/v1/asr -F files=@a.wav -F files=@b.mp3 -F lang=auto   # 官方批量接口
```

### acestep :18012 —— 歌曲生成 (任务制)
```bash
TID=$(curl -s -X POST $HOST:18012/release_task -H 'Content-Type: application/json' -d '{
  "prompt":"失恋但很嗨的电子舞曲, EDM, 128 BPM, 女声主唱",
  "lyrics":"[verse]\n你说再见那天我没有哭\n把你的照片全都删除\n\n[chorus]\n分手快乐 音乐再大声一点\n",
  "vocal_language":"zh","audio_duration":60,"bpm":128,"thinking":true,"audio_format":"mp3"}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["task_id"])')
# 轮询: status 0=进行中 1=成功 2=失败
curl -s -X POST $HOST:18012/query_result -H 'Content-Type: application/json' -d "{\"task_id_list\":[\"$TID\"]}"
# 成功后 result[0].file 形如 /v1/audio?path=...，下载:
curl -s -o song.mp3 "$HOST:18012/v1/audio?path=<path>"
```
完整参数见镜像内 `/app/docs/en/API.md`。

### indextts2 :18013 —— 语音合成 / 克隆
```bash
# OpenAI 兼容 (内置音色 voice_01..voice_09, GET /v1/audio/voices 列出)
curl -s -o out.wav $HOST:18013/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"input":"欢迎使用语音合成服务。","voice":"voice_01","speed":1.0}'
# 指定情绪 (自然语言描述) / 情绪强度
curl -s -o happy.wav $HOST:18013/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"input":"太好了！我们赢了！","voice":"voice_01","instructions":"非常兴奋激动","emo_alpha":0.8}'
# 参考音频克隆 (可附情绪参考音频 emo_wav / 8 维情绪向量 emo_vector / 时长系数 duration_factor)
curl -s -o clone.wav $HOST:18013/v1/audio/clone -F "input=用参考音色朗读这句话。" -F prompt_wav=@ref.wav
```
`speed` 0.5~2 (内部换算成 duration_factor)，`response_format` wav|mp3|flac，`lang` ZH|EN|JA|ES|AR。

### anisora :18014 —— 动漫图生视频 (任务制)
```bash
ID=$(curl -s $HOST:18014/v1/videos -F "prompt=动漫风格, 角色缓缓转头看向镜头并微笑, 镜头缓慢推近" \
      -F image=@first_frame.png -F size=832*480 -F frame_num=81 \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s $HOST:18014/v1/videos/$ID            # status: queued|running|succeeded|failed
curl -s -o out.mp4 $HOST:18014/v1/videos/$ID/content
```
可传多张图并用 `positions=0,1` 指定为首帧/尾帧 (0.5=中间帧)。size: 832*480 / 480*832 / 1280*720 / 720*1280。
V3.2 只有图生视频权重；纯文生视频需先用生图模型画首帧。任务串行执行，同时只跑一个。

---

## 三、从源码重建镜像
```bash
./svc.sh build sensevoice    # 先由 svc.sh models 从 ModelScope 下载 iic/SenseVoiceSmall + fsmn-vad 到 sensevoice/models/, 再 COPY 进镜像
./svc.sh build acestep       # acestep/ 下放官方源码 (github.com/ace-step/ACE-Step-1.5), 构建时用官方下载器从 ModelScope 拉 ACE-Step/Ace-Step1.5 + acestep-v15-xl-turbo
./svc.sh build indextts2     # indextts2/index-tts/ 放官方源码 (github.com/index-tts/index-tts), 构建时拉 ModelScope IndexTeam/IndexTTS-2.5 并预热下载辅助模型
./svc.sh build anisora       # anisora/src/anisoraV3.2 放官方源码 (github.com/bilibili/Index-anisora 的 anisoraV3.2 目录), 构建时拉 ModelScope IndexTeam/Index-anisora 的 V3.2
./svc.sh push                # 推到 $REGISTRY; 换仓库: REGISTRY=新前缀 SRC_REGISTRY=ccr.ccs.tencentyun.com/preview ./svc.sh push
```
- 所有镜像基于 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/vllm/vllm-openai:v0.25.1` (只借用其 Ubuntu22.04 + CUDA + ffmpeg + uv)，pip 走清华源。
- 源码目录需自行放入 (acestep/、indextts2/index-tts/、anisora/src/)，本仓库只含 Dockerfile、服务代码和补丁。
- **权重为什么打进镜像**: 拉下来就能离线直接跑，不依赖外网/ModelScope，部署和迁移一步到位；代价是镜像大。
  acestep / indextts2 / anisora 的权重在 Dockerfile 的 RUN 里直接下载到镜像层，宿主机不留第二份。
- AniSora 官方 V3.2 两个专家是 FP32 (各 57GB)。构建时 `anisora/fetch_bf16.py` 用 HTTP Range 流式拉取、逐张量转成 BF16 分片
  (Wan2.2 本来就按 bf16 推理，结果无损)，126GB → 69GB，过程中不落 FP32 原文件。
- AniSora 补丁: `anisora/patch_sdpa.py` (没有 flash-attn 时回退到 PyTorch SDPA)；`anisora/server.py` 里让非 0 号 rank 不读权重、由 FSDP 从 rank0 广播 (内存从 5×29GB 降到 1×29GB)，T5 放 CPU、换专家时回收显存 (24GB 卡上不这样会 OOM)。

---

## 四、常见问题
- **容器里 `Failed to initialize NVML: Unknown Error`**: 宿主机执行过 `systemctl daemon-reload`。本 compose 已显式挂设备文件可防；已经丢了就 `docker restart lc-<服务>`。尽量不要在跑着 GPU 容器的机器上 daemon-reload。
- **nvidia-smi 卡号和 /dev/nvidiaN 对不上**: 正常现象，见第 3 步按 PCI 总线号对。
- **AniSora OOM**: 24GB 卡至少 5 张；分辨率用 832*480；不要同时在这些卡上跑别的东西。
- **端口**: 只映射到宿主机端口，没有鉴权。要对外请在前面加带口令的反向代理，不要直接暴露公网。
- **日志**: `./svc.sh logs <服务>`；容器日志单文件 50MB×3 轮转。

## 五、许可证
| 服务 | 许可证 | 商用 |
|---|---|---|
| SenseVoice | 代码 MIT；权重 FunASR Model License v1.1 | 官方澄清: 遵守许可即可商用，需保留署名和模型名 |
| ACE-Step 1.5 | MIT (代码+权重) | 可以 |
| IndexTTS-2.5 | bilibili Model Use License (代码+权重) | 可免费商用；月活 >1 亿或年营收 >10 亿元需另行申请；不得用其输出改进其它商用 AI 模型；须保留版权声明和协议副本。不是 OSI 开源许可 |
| Index-AniSora V3.2 | Apache-2.0 (基于 Wan2.2，亦 Apache-2.0) | 可以 |
本仓库自己的脚本 (svc.sh / server.py / Dockerfile / 补丁) 随各服务原项目许可证使用。

## 目录
```
svc.sh  docker-compose.yml  .env.example  README.md
sensevoice/   Dockerfile  server.py
acestep/      Dockerfile.litecode            (+ 官方源码)
indextts2/    Dockerfile  server.py          (+ index-tts/ 官方源码)
anisora/      Dockerfile  server.py  fetch_bf16.py  patch_sdpa.py  (+ src/anisoraV3.2 官方源码)
testdata/     zh_test.wav  ace_test.py  ace_edm.json  tts_test.sh  anisora_test.sh
```
