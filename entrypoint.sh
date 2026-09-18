#!/bin/bash
set -e

G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; R='\033[0;31m'; B='\033[1m'; NC='\033[0m'
info()  { echo -e "${C}[entry]${NC} $*"; }
ok()    { echo -e "${G}[entry]${NC} $*"; }
warn()  { echo -e "${Y}[entry]${NC} $*"; }
error() { echo -e "${R}[entry]${NC} $*" >&2; }

# ── 环境变量 → config.json ────────────────────────────────────
# [v12.3-fix] 只在 config.json 缺失关键字段/是占位符时才注入
# 避免每次重启都覆盖用户通过 Web UI 切换的模型配置
inject_env() {
    local cfg="/opt/litecode/config.json"
    [ ! -f "$cfg" ] && return
    python3 << PYEOF
import json, os
try:
    c = json.loads(open('$cfg').read())
    env = {
        'BACKEND_URL':       os.environ.get('BACKEND_URL',''),
        'API_KEY':           os.environ.get('API_KEY',''),
        'MODEL_ID':          os.environ.get('MODEL_ID',''),
        'SERVER_TOKEN':      os.environ.get('SERVER_TOKEN',''),
        'WEB_PASSWORD':      os.environ.get('WEB_PASSWORD',''),
        'WX_KEEPALIVE_HOURS':os.environ.get('WX_KEEPALIVE_HOURS',''),
        'WX_KEEPALIVE_MSG':  os.environ.get('WX_KEEPALIVE_MSG',''),
    }

    # 占位符集合 — 这些值视为"未配置"
    _placeholders = ('', 'EMPTY', 'sk-your-api-key-here',
                     'your-api-key', 'changeme', 'REPLACE_ME')

    def _is_placeholder(v):
        return not v or v in _placeholders

    c.setdefault('model', {})
    # backend_url: 只在 config 里是空/占位符时才用 env
    if env['BACKEND_URL'] and _is_placeholder(c['model'].get('backend_url', '')):
        c['model']['backend_url'] = env['BACKEND_URL']
        print(f'  [inject] backend_url = {env["BACKEND_URL"]} (config was empty)')
    # api_key: 同上
    if env['API_KEY'] and env['API_KEY'] not in _placeholders \
       and _is_placeholder(c['model'].get('api_key', '')):
        c['model']['api_key'] = env['API_KEY']
        print(f'  [inject] api_key = ***{env["API_KEY"][-4:]} (config was empty)')
    # model_id: 同上
    if env['MODEL_ID'] and _is_placeholder(c['model'].get('id', '')):
        c['model']['id'] = env['MODEL_ID']
        print(f'  [inject] model_id = {env["MODEL_ID"]} (config was empty)')

    # server token 照常注入（这个是服务鉴权，不是模型相关）
    if env['SERVER_TOKEN']:
        c.setdefault('server', {})
        c['server']['token'] = env['SERVER_TOKEN']
    # Web UI 密码
    if env['WEB_PASSWORD']:
        c.setdefault('web_ui', {}).setdefault('auth', {})
        c['web_ui']['auth']['enabled']  = True
        c['web_ui']['auth']['password'] = env['WEB_PASSWORD']
    # 微信保活
    if env['WX_KEEPALIVE_HOURS']:
        c.setdefault('wechat', {})
        try:
            c['wechat']['keepalive_hours'] = int(env['WX_KEEPALIVE_HOURS'])
        except ValueError:
            pass
    if env['WX_KEEPALIVE_MSG']:
        c.setdefault('wechat', {})
        c['wechat']['keepalive_message'] = env['WX_KEEPALIVE_MSG']

    # [v12.3-fix] 校验顶层 model 与 models[] 数组的一致性
    # 如果顶层 model.id 在 models[] 里存在，以 models[] 为准（因为 Web UI 切换改的是数组）
    # [双-entry-fix 2026-06-22] 同 id 双 entry (如 deepseek-v4-pro 直连 + gether 网关)
    # 必须按 (id, backend_url) 双键匹配, 否则永远命中数组里第一条, Web UI 切到的另一条被覆盖
    active_id  = c.get('model', {}).get('id', '')
    active_url = (c.get('model', {}).get('backend_url') or '').rstrip('/')
    if active_id and c.get('models'):
        match = None
        # 优先双键匹配
        if active_url:
            for m in c['models']:
                if m.get('id') == active_id and (m.get('backend_url') or '').rstrip('/') == active_url:
                    match = m; break
        # id-only 兜底
        if match is None:
            for m in c['models']:
                if m.get('id') == active_id:
                    match = m; break
        if match is not None:
            c['model'] = dict(match)
            c['model'].pop('label', None)
            print(f'  [sync] model[{active_id} @ {match.get("backend_url","")}] synced from models[]')

    open('$cfg','w').write(json.dumps(c, ensure_ascii=False, indent=4))
    print('  [inject] done')
except Exception as e:
    print(f'config inject warning: {e}')
PYEOF
    info "config.json 注入/校验完成"
}

# ── VNC 常驻 ──────────────────────────────────────────────────
start_vnc() {
    info "启动 Xvfb + x11vnc + noVNC ..."

    # [v1.10] 创建 .Xauthority (pyautogui import 需要 / desktop_control 可用)
    touch /root/.Xauthority 2>/dev/null || true

    # Xvfb
    # [desktop-2026-05-29] 容器异常退出会留 stale X99 abstract socket (ss 能看到 LISTEN
    # 但 /tmp/.X11-unix/ 是空的, Xvfb 启动报 "server already running")
    # 启动前先清: 干掉残留进程 + 删 lock + 重建 socket dir
    if ! pgrep -x Xvfb > /dev/null 2>&1; then
        rm -f /tmp/.X99-lock 2>/dev/null
        mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix 2>/dev/null
        Xvfb :99 -screen 0 1280x720x24 -nolisten tcp > /tmp/xvfb.log 2>&1 &
        sleep 1
        # 验证 Xvfb 真起来: 没起来就降级到 :100 (abstract socket 偶发占用)
        if ! pgrep -x Xvfb > /dev/null 2>&1; then
            warn "Xvfb :99 启动失败 (abstract socket 占用), 降级 :100"
            Xvfb :100 -screen 0 1280x720x24 -nolisten tcp > /tmp/xvfb.log 2>&1 &
            sleep 1
            export DISPLAY=:100
        else
            export DISPLAY=:99
        fi
    else
        export DISPLAY=:99
    fi
    # xauth 生成 magic cookie 让 pyautogui 能 connect
    if command -v xauth > /dev/null 2>&1; then
        MCOOKIE=$(mcookie 2>/dev/null || echo "$(date +%s)$(echo $RANDOM)$(echo $RANDOM)")
        xauth add :99 . "$MCOOKIE" 2>/dev/null || true
    fi

    # [desktop-2026-05] 起 fluxbox 当窗口管理器 + xterm 让桌面"有东西"
    # 没 WM 时 Xvfb 是裸 X root, noVNC 看着白茫茫. fluxbox 启动后 chromium 弹出来才有窗口框.
    if command -v fluxbox > /dev/null 2>&1 && ! pgrep -x fluxbox > /dev/null 2>&1; then
        DISPLAY=:99 fluxbox > /tmp/fluxbox.log 2>&1 &
        sleep 0.5
        # 起一个 xterm 让桌面有个起手 (用户能直接敲命令)
        if command -v xterm > /dev/null 2>&1; then
            DISPLAY=:99 xterm -geometry 100x30+30+30 -title "LiteCode Desktop" > /dev/null 2>&1 &
        fi
    fi

    # [desktop-2026-05] Chrome user-data 预热: 第一次启动 chrome 会弹"欢迎使用 Google Chrome"
    # 默认浏览器/崩溃报告对话框, 挡住页面. 预先跑一次 first-run + 立即 kill, 把 flag 持久化.
    # 后续 desktop_exec 跑 chrome 就不再弹.
    if command -v google-chrome > /dev/null 2>&1 && [ ! -f /root/.config/google-chrome/First\ Run ]; then
        info "Chrome user-data 预热 (消化 first-run 欢迎弹窗)..."
        mkdir -p /root/.config/google-chrome
        # 跑 chrome 一次, 用 --no-first-run --no-default-browser-check 创建配置目录
        # 用 about:blank 不需要网络, 1 秒后 kill
        DISPLAY=:99 timeout 4 google-chrome \
            --no-sandbox --no-first-run --no-default-browser-check \
            --disable-default-apps --disable-dev-shm-usage \
            --headless about:blank > /tmp/chrome-prime.log 2>&1 || true
        # 显式写 First Run flag 文件 (chrome 看到这文件就跳 first-run)
        touch /root/.config/google-chrome/First\ Run 2>/dev/null || true
        ok "Chrome first-run flag 已写入"
    fi

    # x11vnc
    if ! pgrep -x x11vnc > /dev/null 2>&1; then
        x11vnc -display :99 -forever -nopw -shared \
            -rfbport ${VNC_PORT:-5999} \
            -bg -o /tmp/x11vnc.log 2>/dev/null || true
    fi

    # noVNC websocket
    if ! pgrep -f "websockify.*${NOVNC_PORT:-18800}" > /dev/null 2>&1; then
        websockify --web=/opt/noVNC ${NOVNC_PORT:-18800} \
            localhost:${VNC_PORT:-5999} > /tmp/novnc.log 2>&1 &
    fi

    ok "noVNC → http://0.0.0.0:${NOVNC_PORT:-18800}"
}

# ── SearXNG 健康提示 (sidecar 由 docker-compose 起, 同栈 127.0.0.1:18888) ─
# LiteCode 内 web_search 会优先调本机 SearXNG, 不通自动 fallback playwright.
check_searxng() {
    if curl -sf -m 2 -o /dev/null "http://127.0.0.1:18888/" 2>/dev/null; then
        ok "SearXNG sidecar 已就绪 (http://127.0.0.1:18888)"
    else
        info "SearXNG sidecar 未启动 (compose 未起或还在启动) — web_search 走 playwright fallback"
    fi
}

# ── LiteCode Server ───────────────────────────────────────────
start_server() {
    info "启动 LiteCode Server (端口 ${SERVER_PORT:-18789})..."
    cd /opt/litecode
    python3 litecode_server.py &
    SERVER_PID=$!

    local token="${SERVER_TOKEN:-CHANGE_ME_TOKEN}"
    local port="${SERVER_PORT:-18789}"
    for i in $(seq 1 30); do
        if curl -sf -H "Authorization: Bearer $token" \
            "http://127.0.0.1:$port/health" > /dev/null 2>&1; then
            ok "LiteCode Server 就绪 (PID=$SERVER_PID)"
            return 0
        fi
        sleep 2
    done
    warn "LiteCode Server 启动超时，继续运行..."
}

# ── Web UI（内含 WeChat Bridge 自动启动）─────────────────────
start_webui() {
    info "启动 Web UI + WeChat Bridge (端口 ${WEB_PORT:-18790})..."
    cd /opt/litecode
    python3 web_ui.py &
    WEBUI_PID=$!
    # [v12.6-opt] 真正做 ready check，不是 sleep 1 就当好了
    local port="${WEB_PORT:-18790}"
    for i in $(seq 1 15); do
        if curl -sf "http://127.0.0.1:$port/" > /dev/null 2>&1; then
            ok "Web UI 就绪 (PID=$WEBUI_PID)"
            return 0
        fi
        sleep 2
    done
    warn "Web UI 启动超时，继续运行..."
}

# ── 目录 ──────────────────────────────────────────────────────
init_dirs() {
    mkdir -p \
        /tmp/litecode_workspace/sessions \
        /tmp/litecode_workspace/logs \
        /tmp/litecode_workspace/uploads \
        /root/.litecode/web_sessions \
        /root/.litecode/wechat_media \
        /root/.wechatbot
}

# ── 信号 ──────────────────────────────────────────────────────
SERVER_PID=0
WEBUI_PID=0
SHUTDOWN=0
cleanup() {
    info "停止信号，清理..."
    SHUTDOWN=1
    [ $SERVER_PID -gt 0 ] && kill $SERVER_PID 2>/dev/null
    [ $WEBUI_PID -gt 0 ] && kill $WEBUI_PID 2>/dev/null
    # [v12.6-opt] 原来只 kill 两个 python 进程，VNC 三件套没处理
    pkill -f "websockify.*${NOVNC_PORT:-18800}" 2>/dev/null
    pkill -x x11vnc 2>/dev/null
    pkill -x Xvfb 2>/dev/null
    wait 2>/dev/null
    ok "已停止"
    exit 0
}
trap cleanup SIGTERM SIGINT SIGQUIT

# ── 主入口 ────────────────────────────────────────────────────
main() {
    local mode="${1:-all}"

    echo ""
    echo -e "${B}╔══════════════════════════════════════════╗${NC}"
    echo -e "${B}║  LiteCode v12-r1 + WeChat Bridge         ║${NC}"
    echo -e "${B}║  Mode: ${G}${mode}${NC}${B}                                ║${NC}"
    echo -e "${B}╚══════════════════════════════════════════╝${NC}"
    echo ""

    init_dirs
    inject_env

    case "$mode" in
        all)
            start_vnc
            check_searxng    # [2026-05-25] SearXNG sidecar 健康提示 (由 compose 起)
            start_server
            start_webui

            echo ""
            echo -e "${G}  ✅ 所有服务已启动${NC}"
            echo -e "  Server  : ${C}http://0.0.0.0:${SERVER_PORT:-18789}${NC}"
            echo -e "  Web UI  : ${C}http://0.0.0.0:${WEB_PORT:-18790}${NC}"
            echo -e "  noVNC   : ${C}http://0.0.0.0:${NOVNC_PORT:-18800}${NC}"
            echo ""

            # 前台 wait — 任一进程退出则自动重拉
            while [ $SHUTDOWN -eq 0 ]; do
                # [v12.6-opt] SHUTDOWN 标志让 SIGTERM 能干净退出 while 循环
                # 检查核心进程
                if [ $SERVER_PID -gt 0 ] && ! kill -0 $SERVER_PID 2>/dev/null; then
                    [ $SHUTDOWN -eq 1 ] && break
                    warn "Server 退出，重启..."
                    start_server
                fi
                if [ $WEBUI_PID -gt 0 ] && ! kill -0 $WEBUI_PID 2>/dev/null; then
                    [ $SHUTDOWN -eq 1 ] && break
                    warn "WebUI 退出，重启..."
                    start_webui
                fi
                sleep 5
            done
            ;;
        server)
            start_vnc
            start_server
            wait $SERVER_PID
            ;;
        webui)
            start_vnc
            start_webui
            wait $WEBUI_PID
            ;;
        vnc)
            start_vnc
            tail -f /tmp/x11vnc.log /tmp/novnc.log 2>/dev/null || sleep infinity
            ;;
        cli)
            start_vnc
            start_server
            sleep 3
            cd /opt/litecode
            exec python3 cli.py
            ;;
        bash|shell)
            start_vnc
            exec /bin/bash
            ;;
        *)
            error "未知: $mode  可用: all|server|webui|vnc|cli|bash"
            exit 1
            ;;
    esac
}

main "$@"
