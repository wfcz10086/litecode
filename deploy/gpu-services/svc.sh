#!/usr/bin/env bash
# litecode-svc 统一管理脚本
#   ./svc.sh list                     服务清单表
#   ./svc.sh pull   [服务]            从镜像仓库拉镜像 (权重已在镜像内)
#   ./svc.sh up     [服务]            启动 (docker compose, restart=unless-stopped, 不构建)
#   ./svc.sh down   [服务]            停止并删除容器 (保留镜像)
#   ./svc.sh status                   每个服务: 状态/健康/镜像大小/端口/显卡/显存
#   ./svc.sh logs   <服务> [行数]     跟踪日志
#   ./svc.sh test   [服务]            冒烟测试 (本机 127.0.0.1)
#   ./svc.sh build  [服务]            从源码构建镜像 (构建期下载权重进镜像)
#   ./svc.sh models [服务]            sensevoice: 下载/补齐 models/; 其余: 列出镜像内权重
#   ./svc.sh push   [服务]            推送到 $REGISTRY (REGISTRY_USER/REGISTRY_PASS 给了就先 login; DRY_RUN=1 只打印)
# 环境变量 (可写在同目录 .env, 参考 .env.example):
#   REGISTRY  镜像仓库前缀, 默认 ccr.ccs.tencentyun.com/preview      TAG  镜像标签, 默认 v1
#   SRC_REGISTRY  push 时本地镜像的前缀 (默认同 REGISTRY; 换仓库推送时设成原前缀)
# 服务名: sensevoice acestep indextts2 anisora
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
ROOT=$PWD
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }
export REGISTRY=${REGISTRY:-ccr.ccs.tencentyun.com/preview}
export TAG=${TAG:-v1}
SRC_REGISTRY=${SRC_REGISTRY:-$REGISTRY}
PROJECT=litecode-svc
ALL=(sensevoice acestep indextts2 anisora)

# 服务表: 构建目录|Dockerfile|容器端口→宿主端口变量|默认宿主端口|默认显卡|许可证
declare -A META=(
  [sensevoice]="sensevoice|Dockerfile|SENSEVOICE_PORT|18011|SENSEVOICE_GPU|5|代码 MIT / 权重 FunASR Model License"
  [acestep]="acestep|Dockerfile.litecode|ACESTEP_PORT|18012|ACESTEP_GPU|6|MIT"
  [indextts2]="indextts2|Dockerfile|INDEXTTS2_PORT|18013|INDEXTTS2_GPU|5|bilibili Model Use License"
  [anisora]="anisora|Dockerfile|ANISORA_PORT|18014|ANISORA_GPUS|7,8,9,10,11|Apache-2.0"
)
field() { IFS='|' read -r -a f <<<"${META[$1]}"; echo "${f[$2]}"; }
port()  { local v; v=$(field $1 2); echo "${!v:-$(field $1 3)}"; }
gpus()  {
  if [ "$1" = anisora ]; then echo "${ANISORA_GPU_1:-7},${ANISORA_GPU_2:-8},${ANISORA_GPU_3:-9},${ANISORA_GPU_4:-10},${ANISORA_GPU_5:-11}"
  else local v; v=$(field $1 4); echo "${!v:-$(field $1 5)}"; fi; }
img()   { echo "${2:-$REGISTRY}/litecode-svc-$1:$TAG"; }
pick()  { if [ -n "${1:-}" ]; then [ -n "${META[$1]+x}" ] || { echo "未知服务: $1 (可选: ${ALL[*]})" >&2; exit 2; }; echo "$1"; else echo "${ALL[@]}"; fi; }
dc()    { docker compose -p "$PROJECT" -f "$ROOT/docker-compose.yml" "$@"; }

cmd_list() {
  printf "%-11s %-58s %-6s %-13s %s\n" 服务 镜像 端口 显卡 许可证
  for s in "${ALL[@]}"; do printf "%-11s %-58s %-6s %-13s %s\n" "$s" "$(img $s)" "$(port $s)" "$(gpus $s)" "$(field $s 6)"; done
}

cmd_pull() { for s in $(pick "${1:-}"); do echo "== pull $(img $s)"; docker pull "$(img $s)"; done; }

cmd_models() {
  for s in $(pick "${1:-}"); do
    case $s in
      sensevoice)
        d=$ROOT/sensevoice/models/iic
        if [ ! -s "$d/SenseVoiceSmall/model.pt" ] || [ ! -s "$d/speech_fsmn_vad_zh-cn-16k-common-pytorch/model.pt" ]; then
          echo "[sensevoice] 从 ModelScope 下载权重 → $d"; mkdir -p "$d"
          docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$d:/w" --entrypoint bash \
            swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/vllm/vllm-openai:v0.25.1 -c \
            "modelscope download --model iic/SenseVoiceSmall --local_dir /w/SenseVoiceSmall && \
             modelscope download --model iic/speech_fsmn_vad_zh-cn-16k-common-pytorch --local_dir /w/speech_fsmn_vad_zh-cn-16k-common-pytorch"
        fi
        du -sh "$d"/* ;;
      *)
        if docker image inspect "$(img $s)" >/dev/null 2>&1; then
          case $s in acestep) p=/app/checkpoints ;; indextts2) p=/app/checkpoints ;; anisora) p="/models/V3.2" ;; esac
          echo "[$s] 镜像内权重:"; docker run --rm --entrypoint sh "$(img $s)" -c "du -sh $p/* 2>/dev/null | sort -h | tail -12"
        else
          echo "[$s] 本地没有镜像 $(img $s): ./svc.sh pull $s 或 ./svc.sh build $s"
        fi ;;
    esac
  done
}

cmd_build() {
  for s in $(pick "${1:-}"); do
    [ "$s" = sensevoice ] && cmd_models sensevoice
    echo "== build $s → $(img $s)"
    docker build -f "$ROOT/$(field $s 0)/$(field $s 1)" -t "$(img $s)" "$ROOT/$(field $s 0)"
  done
}

cmd_up()   { if [ -n "${1:-}" ]; then pick "$1" >/dev/null; dc up -d --no-build "$1"; else dc up -d --no-build; fi; }
cmd_down() { if [ -n "${1:-}" ]; then pick "$1" >/dev/null; dc stop "$1"; dc rm -f "$1"; else dc down; fi; }
cmd_logs() { [ -n "${1:-}" ] || { echo "用法: $0 logs <服务> [行数]"; exit 2; }; docker logs -f --tail "${2:-200}" "lc-$1"; }

cmd_status() {
  printf "%-11s %-10s %-10s %-8s %-6s %-13s %s\n" 服务 状态 健康 镜像大小 端口 显卡 "显存(已用/总, 该卡全部进程)"
  for s in "${ALL[@]}"; do
    st=$(docker inspect -f '{{.State.Status}}' "lc-$s" 2>/dev/null) || st=未创建
    hl=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' "lc-$s" 2>/dev/null) || hl=-
    sz=$(docker image inspect -f '{{.Size}}' "$(img $s)" 2>/dev/null) && sz=$(awk -v b="$sz" 'BEGIN{printf "%.1fG", b/1e9}') || sz=无镜像
    g=$(gpus $s)
    mem=$(nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits -i "$g" 2>/dev/null | awk -F', ' '{printf "%s:%s/%sM ", $1,$2,$3}' || true)
    printf "%-11s %-10s %-10s %-8s %-6s %-13s %s\n" "$s" "$st" "$hl" "$sz" "$(port $s)" "$g" "$mem"
  done
}

cmd_push() {
  run() { if [ "${DRY_RUN:-0}" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
  local host=${REGISTRY%%/*}
  if [ -n "${REGISTRY_USER:-}" ] && [ -n "${REGISTRY_PASS:-}" ]; then
    if [ "${DRY_RUN:-0}" = 1 ]; then echo "[dry-run] docker login $host -u $REGISTRY_USER --password-stdin"
    else echo "$REGISTRY_PASS" | docker login "$host" -u "$REGISTRY_USER" --password-stdin; fi
  fi
  for s in $(pick "${1:-}"); do
    local src dst; src=$(img $s "$SRC_REGISTRY"); dst=$(img $s)
    docker image inspect "$src" >/dev/null 2>&1 || { echo "[$s] 本地无镜像 $src"; continue; }
    [ "$src" != "$dst" ] && run docker tag "$src" "$dst"
    echo "== push $dst"; run docker push "$dst"
  done
}

cmd_test() {
  T=$ROOT/testdata
  for s in $(pick "${1:-}"); do
    p=$(port $s); echo "== $s (:$p)"
    case $s in
      sensevoice) curl -s "http://127.0.0.1:$p/v1/audio/transcriptions" -F file=@"$T/zh_test.wav" -F model=sensevoice; echo ;;
      acestep)    curl -s "http://127.0.0.1:$p/health"; echo; ACE_URL="http://127.0.0.1:$p" python3 "$T/ace_test.py" 30 | grep -E "status|saved" ;;
      indextts2)  curl -s -o /tmp/_tts.wav -w "HTTP %{http_code} %{size_download}B %{time_total}s\n" "http://127.0.0.1:$p/v1/audio/speech" \
                    -H 'Content-Type: application/json' -d '{"input":"你好，这是一段语音合成测试。","voice":"voice_01"}' ;;
      anisora)    curl -s "http://127.0.0.1:$p/health"; echo ;;
    esac
  done
}

c=${1:-list}; shift || true
case $c in
  list|pull|build|up|down|status|logs|models|push|test) "cmd_$c" "$@" ;;
  *) sed -n '2,17p' "$0"; exit 2 ;;
esac
