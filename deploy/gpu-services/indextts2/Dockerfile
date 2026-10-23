# IndexTTS-2.5 (bilibili Model Use License) 零样本语音合成/克隆 — 权重打进镜像
# 基础: 本机已有 vllm-openai 镜像 (只借用其 Ubuntu22.04 + CUDA 运行库 + ffmpeg + uv); 项目自身用 uv 建 py3.11 虚拟环境
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/vllm/vllm-openai:v0.25.1
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PYTHON_INSTALL_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone \
    UV_HTTP_TIMEOUT=600 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python HF_ENDPOINT=https://hf-mirror.com
WORKDIR /app
COPY index-tts/pyproject.toml index-tts/uv.lock index-tts/.python-version index-tts/README.md /app/
RUN uv python install 3.11 && uv sync --no-dev --no-install-project --no-cache
COPY index-tts/ /app/
RUN uv sync --no-dev --no-cache && uv pip install --no-cache --python /opt/venv/bin/python fastapi "uvicorn[standard]" python-multipart soundfile
# 权重 (ModelScope) + 首次初始化会拉取的辅助模型 (w2v-bert / MaskGCT / campplus / bigvgan) + 示例音色, 全部在构建期下载进镜像
RUN /opt/venv/bin/modelscope download --model IndexTeam/IndexTTS-2.5 --local_dir /app/checkpoints \
 && cd /app && /opt/venv/bin/python -c "\
from indextts.utils.examples_downloader import ensure_examples_available; ensure_examples_available(); \
from indextts.infer_v2_5 import IndexTTS2; IndexTTS2(cfg_path='checkpoints/config.yaml', model_dir='checkpoints', device='cpu'); print('warmup ok')" \
 && ls examples | head -30 && du -sh /app/checkpoints /root/.cache/* 2>/dev/null
COPY server.py /app/server.py
ENV INDEXTTS_DEVICE=cuda:0 HF_HUB_OFFLINE=1
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=300s --retries=3 CMD curl -sf http://localhost:8000/health || exit 1
ENTRYPOINT []
CMD ["/opt/venv/bin/python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
