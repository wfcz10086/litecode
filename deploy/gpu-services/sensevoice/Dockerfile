# SenseVoiceSmall (FunASR) 语音识别 — 权重打进镜像 (/models/iic/...)
# 基础: 本机已有 vllm-openai 镜像 (Ubuntu22.04 + py3.12 + torch 2.11 cu130 + ffmpeg), 与 acestep/indextts2 共享底层
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/vllm/vllm-openai:v0.25.1
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_DISABLE_PIP_VERSION_CHECK=1
RUN echo "torch==2.11.0+cu130" > /tmp/c.txt && \
    pip install --no-cache-dir torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130 -c /tmp/c.txt && \
    pip install --no-cache-dir -c /tmp/c.txt "funasr>=1.2" soundfile python-multipart
# 权重: models/ 由 svc.sh models sensevoice 从 ModelScope 下载 (iic/SenseVoiceSmall + iic/speech_fsmn_vad_zh-cn-16k-common-pytorch)
COPY models/iic /models/iic
WORKDIR /app
COPY server.py /app/server.py
ENV SENSEVOICE_DEVICE=cuda:0 MODELSCOPE_CACHE=/models
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 CMD curl -sf http://localhost:8000/health || exit 1
ENTRYPOINT ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
