# Index-AniSora V3.2 (Apache-2.0) 动漫视频生成 — 基于 Wan2.2 i2v-A14B, 多卡 FSDP + Ulysses 常驻服务
# 基础: 本机已有 vllm-openai 镜像 (py3.12 + torch 2.11 cu130 + ffmpeg), 与其它 litecode-svc 镜像共享底层
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/vllm/vllm-openai:v0.25.1
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_DISABLE_PIP_VERSION_CHECK=1
RUN echo "torch==2.11.0+cu130" > /tmp/c.txt && pip install --no-cache-dir -c /tmp/c.txt \
      "diffusers>=0.31.0" easydict ftfy "imageio[ffmpeg]" imageio-ffmpeg opencv-python-headless python-multipart dashscope
# 权重 (ModelScope IndexTeam/Index-anisora 的 V3.2 目录): 两个 57GB 的 FP32 专家在构建期用 Range 流式转成 BF16 分片
# (Wan2.2 推理本就按 bf16 运行, 结果无损; 体积 126GB → 69GB), T5/VAE/tokenizer 原样下载
COPY fetch_bf16.py /tmp/fetch_bf16.py
RUN set -e; R=IndexTeam/Index-anisora; D=/models/V3.2; mkdir -p $D \
 && modelscope download --model $R --local_dir /models --include "V3.2/Wan2.1_VAE.pth" "V3.2/models_t5_umt5-xxl-enc-bf16.pth" "V3.2/google/*" "V3.2/configuration.json" "V3.2/high_noise_model/config.json" "V3.2/low_noise_model/config.json" \
 && for e in high_noise_model low_noise_model; do python3 /tmp/fetch_bf16.py $R V3.2/$e/diffusion_pytorch_model.safetensors $D/$e; done \
 && rm -rf /root/.cache/modelscope /models/._____temp && du -sh $D/*
WORKDIR /app
COPY src/anisoraV3.2 /app/anisoraV3.2
# 无 flash-attn 轮子 (torch 2.11+cu130): flash_attention 回退到 SDPA (带 key padding mask)
COPY patch_sdpa.py /tmp/patch_sdpa.py
RUN python3 /tmp/patch_sdpa.py /app/anisoraV3.2/wan/modules/attention.py
COPY server.py /app/server.py
ENV ANISORA_NPROC=5 ANISORA_CKPT=/models/V3.2 TOKENIZERS_PARALLELISM=false PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=900s --retries=3 CMD curl -sf http://localhost:8000/health || exit 1
ENTRYPOINT []
CMD ["sh", "-c", "exec torchrun --standalone --nproc_per_node=${ANISORA_NPROC} /app/server.py"]
