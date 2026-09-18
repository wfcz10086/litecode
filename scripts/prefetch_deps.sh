#!/usr/bin/env bash
# scripts/prefetch_deps.sh — v1.9 P40 国内构建加速预拉
#
# 用途：
#   docker build 前一次性把大依赖（playwright chromium）预先拉到 vendor/
#   后续 docker build 会从 vendor/ COPY 跳过远程下载
#
# 用法：
#   bash scripts/prefetch_deps.sh                  # 默认：playwright chromium
#   bash scripts/prefetch_deps.sh --novnc          # 同时刷新 noVNC（已 vendor，仅在升级时跑）
#   bash scripts/prefetch_deps.sh --all            # 全部
#
# 说明：
#   - 优先使用国内镜像（npmmirror）
#   - 失败时回退默认源
#   - 二进制不入 git（.gitignore 已排除）

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor"
PLAYWRIGHT_CACHE="$VENDOR/playwright-cache"
NOVNC_DIR="$VENDOR/noVNC"

DO_PLAYWRIGHT=true
DO_NOVNC=false

case "${1:-}" in
    --novnc) DO_PLAYWRIGHT=false; DO_NOVNC=true ;;
    --all)   DO_PLAYWRIGHT=true;  DO_NOVNC=true ;;
    --help|-h)
        head -20 "$0" | tail -19
        exit 0
        ;;
esac

# ── playwright chromium 预拉 ────────────────────────────────
if $DO_PLAYWRIGHT; then
    echo "[prefetch] playwright chromium → $PLAYWRIGHT_CACHE"
    mkdir -p "$PLAYWRIGHT_CACHE"
    export PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"
    export PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE"

    if ! command -v python3 > /dev/null; then
        echo "[prefetch] ERROR: python3 not found"; exit 1
    fi

    if ! python3 -c "import playwright" 2>/dev/null; then
        echo "[prefetch] 安装 playwright..."
        pip3 install playwright -i https://mirrors.aliyun.com/pypi/simple/ --break-system-packages \
            2>&1 | tail -3
    fi

    echo "[prefetch] 下载 chromium 到 $PLAYWRIGHT_BROWSERS_PATH ..."
    python3 -m playwright install chromium 2>&1 | tail -10

    if ls "$PLAYWRIGHT_CACHE"/chromium-*/chrome-linux*/chrome 2>/dev/null > /dev/null; then
        SIZE=$(du -sh "$PLAYWRIGHT_CACHE" | cut -f1)
        echo "[prefetch] ✅ playwright cache 就绪 ($SIZE)"
    else
        echo "[prefetch] ⚠️ chromium 二进制未找到，可能下载失败"
    fi
fi

# ── noVNC 升级 ──────────────────────────────────────────────
if $DO_NOVNC; then
    echo "[prefetch] 刷新 noVNC → $NOVNC_DIR"
    if [ -d "$NOVNC_DIR" ]; then
        rm -rf "$NOVNC_DIR"
    fi
    git clone --depth 1 https://github.com/novnc/noVNC.git "$NOVNC_DIR" 2>&1 | tail -3
    rm -rf "$NOVNC_DIR/.git"
    SIZE=$(du -sh "$NOVNC_DIR" | cut -f1)
    echo "[prefetch] ✅ noVNC 就绪 ($SIZE)"
fi

echo "[prefetch] DONE"
