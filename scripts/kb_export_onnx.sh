#!/bin/bash
# 向量模型 PyTorch → ONNX 一次性导出 (在临时 python:3.12-slim 容器里装 torch CPU 跑, 宿主和 litecode 镜像都不用装 torch)。
#   bash scripts/kb_export_onnx.sh [源目录] [输出目录]
# 源目录默认 /home/ubuntu/kb/text2vec-base-chinese-paraphrase (用户原知识库项目自带的模型, 也可从
# https://huggingface.co/shibing624/text2vec-base-chinese-paraphrase 下载); 输出默认 workspace/kb/models/… (不入 git)。
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(realpath "${1:-/home/ubuntu/kb/text2vec-base-chinese-paraphrase}")
OUT=$(realpath -m "${2:-$HERE/../workspace/kb/models/text2vec-base-chinese-paraphrase-onnx}")
mkdir -p "$OUT"
docker run --rm -v "$SRC":/src:ro -v "$OUT":/out -v "$HERE/kb_export_onnx.py":/export.py:ro python:3.12-slim bash -c \
  'pip install -q --index-url https://download.pytorch.org/whl/cpu torch==2.4.1 \
   && pip install -q transformers==4.44.2 onnx "numpy<2" \
   && python /export.py /src /out && chown -R '"$(id -u):$(id -g)"' /out'
ls -la "$OUT"
