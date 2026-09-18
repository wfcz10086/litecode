#!/bin/bash
# LiteCode v12-r1 一键启动脚本
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAR_NAME="litecodeext_v12.tar.gz"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$SCRIPT_DIR/.env"
IMAGE_TAG="litecode:v12-r1"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

usage() {
cat << EOF
${BOLD}LiteCode v12-r1 启动脚本${NC}

  ./start.sh              自动检测，有镜像直接启动，没有则构建
  ./start.sh --build      强制重新构建
  ./start.sh --down       停止服务
  ./start.sh --restart    重启
  ./start.sh --logs       实时日志
  ./start.sh --status     状态检查
  ./start.sh --shell      进入容器
  ./start.sh --help       帮助

EOF
}

check_deps() {
    command -v docker &>/dev/null || die "未找到 docker"
    docker compose version &>/dev/null 2>&1 || \
    docker-compose version &>/dev/null 2>&1 || \
    die "未找到 docker compose"
}

COMPOSE() {
    if docker compose version &>/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

setup_env() {
    if [ ! -f "$ENV_FILE" ]; then
        cp "$SCRIPT_DIR/.env.example" "$ENV_FILE" 2>/dev/null || true
        warn ".env 已从示例创建，请编辑填写 API_KEY"
    fi
    source "$ENV_FILE" 2>/dev/null || true
    # [v12.6-opt] 检测 API_KEY 是否还是占位符
    case "${API_KEY:-}" in
        ""|"sk-your-api-key-here"|"your-api-key"|"EMPTY"|"REPLACE_ME")
            warn "API_KEY 未配置或仍是占位符（$ENV_FILE）"
            warn "  本地 vLLM/Ollama 可留 EMPTY；用云端模型请填真实 key"
            ;;
    esac
}

setup_dirs() {
    # [v12.6-fix] 去掉笔误的 's' 目录（原来会创建一个无意义的空目录）
    mkdir -p "$SCRIPT_DIR"/{workspace,sessions,web_sessions,wechat_creds,wechat_media,litecode_state/dag_jobs}
    # [migrate-2026-05] 单文件 bind-mount 要求 host source 是文件 (否则 docker 自动建成目录会 mount 错)
    [ -f "$SCRIPT_DIR/litecode_state/timers.json" ] || echo "[]" > "$SCRIPT_DIR/litecode_state/timers.json"
    [ -f "$SCRIPT_DIR/litecode_state/web_auth_tokens.json" ] || echo "{}" > "$SCRIPT_DIR/litecode_state/web_auth_tokens.json"
}

image_exists() {
    docker image inspect "$IMAGE_TAG" &>/dev/null 2>&1
}

do_build() {
    [ ! -f "$SCRIPT_DIR/$TAR_NAME" ] && die "未找到 $TAR_NAME"
    info "构建 $IMAGE_TAG（首次约 10-15 分钟）..."
    cd "$SCRIPT_DIR"
    COMPOSE --env-file "$ENV_FILE" build --progress=plain
    image_exists && ok "构建成功" || die "构建失败"
}

do_up() {
    cd "$SCRIPT_DIR"
    info "启动容器..."
    COMPOSE --env-file "$ENV_FILE" up -d

    source "$ENV_FILE" 2>/dev/null || true
    local port="${SERVER_PORT:-18789}"
    local token="${SERVER_TOKEN:-CHANGE_ME_TOKEN}"

    info "等待 Server 就绪..."
    for i in $(seq 1 30); do
        curl -sf -H "Authorization: Bearer $token" \
            "http://localhost:$port/health" &>/dev/null && break
        printf "."; sleep 3
    done
    echo ""

    echo ""
    echo -e "${BOLD}════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  LiteCode v12-r1 启动成功${NC}"
    echo -e "${BOLD}════════════════════════════════════════════${NC}"
    echo -e "  Web UI  : ${CYAN}http://localhost:${WEB_PORT:-18790}${NC}"
    echo -e "  API     : ${CYAN}http://localhost:${SERVER_PORT:-18789}${NC}"
    echo -e "  noVNC   : ${CYAN}http://localhost:18800${NC}"
    echo -e "  密码    : ${YELLOW}CHANGE_ME_PASSWORD${NC}"
    echo -e "  模型    : ${YELLOW}${MODEL_ID:-glm-5}${NC}"
    echo -e "${BOLD}════════════════════════════════════════════${NC}"
    echo -e "  ./start.sh --logs    查看日志"
    echo -e "  ./start.sh --shell   进入容器"
    echo -e "  ./start.sh --down    停止服务"
    echo ""
}

case "${1:-}" in
    --help|-h)    usage; exit 0 ;;
    --down)       check_deps; cd "$SCRIPT_DIR"; COMPOSE down; ok "已停止"; exit 0 ;;
    --restart)    check_deps; setup_env; cd "$SCRIPT_DIR"; COMPOSE --env-file "$ENV_FILE" restart; ok "已重启"; exit 0 ;;
    --logs)       check_deps; cd "$SCRIPT_DIR"; COMPOSE logs -f --tail=100; exit 0 ;;
    --status)
        check_deps; setup_env; cd "$SCRIPT_DIR"; COMPOSE ps; echo ""
        source "$ENV_FILE" 2>/dev/null || true
        curl -sf -H "Authorization: Bearer ${SERVER_TOKEN:-CHANGE_ME_TOKEN}" \
            "http://localhost:${SERVER_PORT:-18789}/health" &>/dev/null \
            && ok "Server: 在线" || warn "Server: 离线"
        curl -sf "http://localhost:${WEB_PORT:-18790}/" &>/dev/null \
            && ok "Web UI: 在线" || warn "Web UI: 离线"
        exit 0 ;;
    --shell)      check_deps; docker exec -it litecode /bin/bash; exit 0 ;;
    --build)
        check_deps; setup_env; setup_dirs; do_build; do_up ;;
    "")
        check_deps; setup_env; setup_dirs
        if ! image_exists; then
            info "未找到镜像，开始构建..."
            do_build
        fi
        do_up ;;
    *)  die "未知参数: $1  用 --help 查看帮助" ;;
esac
