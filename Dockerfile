# [v1.9] 华为云 swr 镜像加速（国内拉取更快）
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/ubuntu:24.04

LABEL maintainer="LiteCode v12-r1 + WeChat Bridge"
LABEL description="LiteCode OpenClaw AI Agent - Ubuntu 24.04 (国内源加速版)"
LABEL version="v12-r1-cn"

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
# [FIX] 之前 LANG=C.UTF-8 会让 docker logs 的中文/emoji 显示成 `?`
# 改为 zh_CN.UTF-8 + locale-gen 确保 UTF-8 流正确输出
ENV LANG=zh_CN.UTF-8
ENV LC_ALL=zh_CN.UTF-8
ENV LANGUAGE=zh_CN:zh
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=UTF-8
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# ── [v1.9] apt 切阿里源（国内构建加速） ──────────────────────
# Ubuntu 24.04 noble 用 deb822 格式 /etc/apt/sources.list.d/ubuntu.sources
RUN sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; \
            s|http://security.ubuntu.com|http://mirrors.aliyun.com|g; \
            s|http://ports.ubuntu.com|http://mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true && \
    sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; \
            s|http://security.ubuntu.com|http://mirrors.aliyun.com|g; \
            s|http://ports.ubuntu.com|http://mirrors.aliyun.com|g' \
        /etc/apt/sources.list 2>/dev/null || true

# ── [v1.9] pip 切阿里源（全局，避免每个 pip install 加 -i） ──
RUN mkdir -p /root/.pip /etc/pip && \
    printf '[global]\nindex-url = https://mirrors.aliyun.com/pypi/simple/\ntrusted-host = mirrors.aliyun.com\ntimeout = 60\n' \
        > /root/.pip/pip.conf && \
    cp /root/.pip/pip.conf /etc/pip.conf

# ── 系统基础包 ────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git vim nano less \
    ca-certificates gnupg lsb-release \
    build-essential gcc g++ make cmake pkg-config \
    python3 python3-pip python3-dev python3-venv \
    python3-setuptools python3-wheel \
    nodejs npm \
    net-tools iputils-ping netcat-openbsd nmap \
    iproute2 dnsutils telnet traceroute \
    htop iotop sysstat lsof procps \
    zip unzip tar gzip bzip2 xz-utils \
    jq sqlite3 \
    poppler-utils ghostscript \
    libssl-dev libffi-dev \
    libxml2-dev libxslt1-dev \
    libjpeg-dev libpng-dev libfreetype6-dev \
    zlib1g-dev \
    ffmpeg imagemagick \
    fonts-noto-cjk fonts-wqy-microhei fonts-wqy-zenhei \
    golang-go \
    xvfb x11vnc xauth \
    python3-websockify python3-numpy \
    locales tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    locale-gen zh_CN.UTF-8 en_US.UTF-8 && \
    update-locale LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8

# ── [v1.9] noVNC 走本地 vendor（不再 git clone github）────────
COPY vendor/noVNC /opt/noVNC
RUN ln -sf /opt/noVNC/vnc.html /opt/noVNC/index.html

# ── [v1.10] desktop_control 截屏依赖（独立 RUN 保留前面 cache） ──
RUN apt-get update && apt-get install -y --no-install-recommends scrot && \
    rm -rf /var/lib/apt/lists/* || echo "WARN: scrot install 失败"

# ── [preview-2026-05] LibreOffice headless — 用于 Office (pptx/docx/xlsx) 转 PDF 在线预览
# 装 core + impress + writer + calc 子包, 不装 GUI 相关 (~700MB → 没 GUI 后 ~500MB)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-impress libreoffice-writer libreoffice-calc \
        libreoffice-common && \
    rm -rf /var/lib/apt/lists/* || echo "WARN: libreoffice install 失败 (office 预览将不可用)"

# ── [desktop-2026-05] 桌面操控: fluxbox WM + xterm + xdotool + google-chrome ──
# 让 AI 能用 desktop_exec 在 noVNC 桌面里弹浏览器 / 跑 xdotool 操作.
# ⚠ Ubuntu 24.04 把 apt 的 chromium-browser / firefox 全变 snap shim (容器跑不起来),
#   所以浏览器用 google-chrome 官方 deb (deb 包不依赖 snapd).
RUN apt-get update && apt-get install -y --no-install-recommends \
        fluxbox xterm xdotool && \
    rm -rf /var/lib/apt/lists/* || \
    echo "WARN: fluxbox/xterm/xdotool 部分失败"

# google-chrome stable deb (130MB, 比 playwright chromium 镜像稳)
RUN cd /tmp && \
    (curl -fsSL -o google-chrome.deb \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb || \
     curl -fsSL -o google-chrome.deb \
        https://mirrors.aliyun.com/google-chrome/google-chrome-stable_current_amd64.deb) && \
    apt-get update && apt-get install -y --no-install-recommends /tmp/google-chrome.deb && \
    rm -f /tmp/google-chrome.deb && rm -rf /var/lib/apt/lists/* || \
    echo "WARN: google-chrome 安装失败, desktop 浏览器功能受限"

# ── [ssh-2026-05] SSH client — 让 LiteCode AI 能 ssh 到远程机器执行命令 ──
# (docker-compose 把 host ~/.ssh 只读挂载到容器 /root/.ssh, 见 docker-compose.yml volumes)
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client && \
    rm -rf /var/lib/apt/lists/*

# ── Node.js 全局包 ────────────────────────────────────────────
RUN npm install -g \
    pptxgenjs marked \
    @modelcontextprotocol/sdk zod \
    && npm cache clean --force

# ── [v1.9] Python 核心依赖（拆 4 组 + 兜底，避免一个包失败整个 RUN 失败）──
# Group 1: ★Web 框架 + HTTP 必装★（失败要 fail build）
RUN pip3 install --break-system-packages \
    fastapi uvicorn[standard] python-multipart \
    httpx requests aiohttp aiofiles \
    prompt_toolkit psutil pydantic \
    rich click pyyaml toml python-dateutil pytz

# Group 2: 数据科学（分批，单包失败不阻塞）
RUN pip3 install --break-system-packages \
    pandas numpy scipy openpyxl xlsxwriter || \
    pip3 install --break-system-packages pandas numpy scipy openpyxl xlsxwriter || true
RUN pip3 install --break-system-packages \
    matplotlib seaborn plotly tabulate Pillow imageio || \
    echo "WARN: 数据可视化包部分失败，影响 deep-report skill"

# Group 3: PDF/Office（pypdfium2 阿里源偶尔同步滞后）
RUN pip3 install --break-system-packages \
    python-docx python-pptx pypdf pdfplumber \
    reportlab PyMuPDF fpdf2 || true
RUN pip3 install --break-system-packages pypdfium2 || \
    pip3 install -i https://pypi.org/simple/ --break-system-packages pypdfium2 || \
    echo "WARN: pypdfium2 安装失败，pdf-ocr-pipeline skill 退化"

# Group 4: 数据库 + 浏览器 + MCP + 其他（拆开避免一个失败带走全组）
# 4a: HTML 解析（Web UI 必装）
RUN pip3 install --break-system-packages beautifulsoup4 lxml || \
    pip3 install -i https://pypi.org/simple/ --break-system-packages beautifulsoup4 lxml
# 4b: ORM + 数据库驱动（部分功能用）
RUN pip3 install --break-system-packages sqlalchemy redis || true
RUN pip3 install --break-system-packages psycopg2-binary pymysql || \
    echo "WARN: psycopg2/pymysql 失败，PG/MySQL 不可用"
# 4c: 浏览器
RUN pip3 install --break-system-packages selenium || true
# 4d: MCP + 加密 + qrcode
RUN pip3 install --break-system-packages mcp cryptography pycryptodome qrcode || true

# Group 5: 微信桥（pip 包名 wechatbot-sdk, import 名 wechatbot）
RUN pip3 install --break-system-packages wechatbot-sdk==0.2.0 || \
    pip3 install -i https://pypi.org/simple/ --break-system-packages wechatbot-sdk==0.2.0 || \
    echo "WARN: wechatbot-sdk 失败，微信桥不可用"
# 验证 import 名（pip 装 wechatbot-sdk → import wechatbot）
RUN python3 -c "import wechatbot; print('  ✓ wechatbot import OK')" || \
    echo "WARN: wechatbot import 失败"

# Group 6: ★Playwright 必装★（Web E2E 依赖）
RUN pip3 install --break-system-packages playwright && \
    python3 -m playwright install-deps chromium 2>/dev/null || true

# Group 7: desktop_control + LSP（v1.9 P40 真测过的依赖）
RUN pip3 install --break-system-packages pyautogui jedi pyright || \
    echo "WARN: desktop/LSP 工具部分失败"
# 验证关键功能
RUN python3 -c "import jedi; import pyautogui" 2>&1 | head -3 || \
    echo "WARN: jedi/pyautogui 不可用"

# ── [v1.9] Playwright chromium：国内镜像 + 可选本地 vendor cache ──
# 优先使用 vendor/playwright-cache（如果由 prefetch_deps.sh 准备好），
# 否则从 npmmirror.com（淘宝镜像）下载，最后回退到默认源。
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# 总是 COPY vendor/playwright-cache/（默认空，开发者可预填浏览器）
COPY vendor/playwright-cache/ /root/.cache/ms-playwright/

RUN if [ -d /root/.cache/ms-playwright/chromium-1217 ] || \
       [ -d /root/.cache/ms-playwright/chromium-1130 ] || \
       ls /root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1 > /dev/null 2>&1; then \
        echo "[playwright] vendor cache 命中, 跳过下载"; \
    else \
        python3 -m playwright install chromium 2>/dev/null || \
        echo "WARNING: playwright chromium install failed"; \
    fi

# ── 代码部署（单包，无热补丁层）─────────────────────────────
WORKDIR /opt/litecode

#ARG LITECODE_TAR=litecodeext_v12.tar.gz
COPY litecodeext /opt/litecode
RUN mkdir -p \
    /data/workspace /data/sessions /data/logs \
    /root/.litecode/web_sessions \
    /root/.litecode/wechat_media \
    /root/.wechatbot \
    /tmp/litecode_workspace/sessions \
    /tmp/litecode_workspace/logs \
    /tmp/litecode_workspace/uploads

ENV LITECODE_HOME=/opt/litecode
ENV PATH="/opt/litecode:/usr/local/go/bin:${PATH}"
ENV GOPATH=/root/go
ENV DISPLAY=:99
ENV VNC_PORT=5999
ENV NOVNC_PORT=18800

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# [host-mode] EXPOSE 在 network_mode: host 下只起文档作用, 真正端口由 config.json / env 决定
EXPOSE 18789 18790 18800

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf -H "Authorization: Bearer $(python3 -c 'import json;print(json.load(open("/opt/litecode/config.json"))["server"]["token"])' 2>/dev/null || echo CHANGE_ME_TOKEN)" \
        http://localhost:18789/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["all"]
