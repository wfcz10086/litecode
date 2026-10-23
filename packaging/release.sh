#!/usr/bin/env bash
# LiteCode 一键发布 (Windows 桌面版 + 代码更新包)
#
#   bash packaging/release.sh <版本号>          只发代码更新 (最常见, 1–2 分钟): 所有桌面用户托盘提示更新
#   bash packaging/release.sh <版本号> --full   同时出完整离线安装包; 依赖层没变时复用缓存 (几分钟)
#   ... --skip-tests                             显式跳过发布前测试 (会打印醒目警告; 只在紧急修复时用)
#   LITECODE_UPDATE_URL=https://你的下载目录/ bash packaging/release.sh ...   托盘检查更新/下载模型的地址
#                                                (目录里放本脚本产出的 latest.json 和各个包; 不设则用 main.go 里的默认值)
#   LITECODE_DIST_DIR=产物输出目录   LITECODE_NO_PUSH=1 只在本地打标签不推送
#
# 预检之后必须跑发布前测试 (scripts/run_release_tests.sh: litecodeext pytest + tests/smoke.sh + 托盘程序 go test/vet),
# 以退出码判定, 任何失败或超时都停止发布。
#
# 加速设计:
#   - 依赖层 (系统库/浏览器/Python 包, 占 99%) 按指纹缓存在 $CACHE, 只在依赖变了时构建一次并做一次全量隐私扫描;
#   - 代码层 (十几 MB) 每次重打, 和缓存的依赖层 gzip 首尾直接拼接, 不重压 4GB;
#   - 依赖没变时 rootfs_version 不变, 老用户只收到代码更新。
# 产物不进 git (几个 GB), 放发布目录; 脚本、Dockerfile、WSL 配置、go.sum 进 git, 谁拉代码都能一条命令出同样的包。
set -euo pipefail
VER=${1:?用法: release.sh <版本号> [--full] [--skip-tests]}
shift
FULL=""; SKIP_TESTS=0
for a in "$@"; do
  case "$a" in
    --full) FULL=--full ;;
    --skip-tests) SKIP_TESTS=1 ;;
    *) echo "未知参数: $a (可用: --full --skip-tests)"; exit 1 ;;
  esac
done
ROOT=/opt/litecode
HERE=$ROOT/packaging/windows
OUT=${LITECODE_DIST_DIR:-./dist/win}
CACHE=/opt/build/litecode-win/cache
WORK=/opt/build/litecode-win/work/$VER
export PATH=/home/ubuntu/.local/go/bin:$PATH GOPROXY=https://goproxy.cn,direct
mkdir -p "$CACHE" "$WORK" "$OUT"
# 缓存和中间产物都不自动删; 磁盘不够时只提醒, 由人决定删哪些
FREE_GB=$(df -BG --output=avail /opt/build | tail -1 | tr -dc 0-9)
[ "${FREE_GB:-99}" -lt 15 ] && echo "⚠ /opt/build 只剩 ${FREE_GB}G, 完整打包要约 10G; 旧缓存在 $CACHE (deps-*.tar.gz 可按时间删老的)"
T0=$(date +%s)
step(){ echo; echo "[$(( $(date +%s) - T0 ))s] == $*"; }

step "0. 预检 (能提前发现的问题第一秒就报, 别等打了几分钟才在最后一步失败)"
[[ "$VER" =~ ^[0-9]+(\.[0-9]+){1,3}$ ]] || { echo "版本号格式不对: $VER (要 2.0.0 这种点分数字, 最多四段)"; exit 1; }
for t in go docker python3 curl sha256sum; do command -v $t >/dev/null || { echo "缺工具: $t"; exit 1; }; done
if [ "$FULL" = "--full" ]; then
  command -v makensis >/dev/null || { echo "缺工具: makensis (apt install nsis)"; exit 1; }
  docker image inspect litecode:latest >/dev/null 2>&1 || { echo "缺基础镜像 litecode:latest"; exit 1; }
  (cd "$HERE" && GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go vet ./...) || { echo "托盘程序 go vet 不过"; exit 1; }
  for f in $(grep -oE 'File "[^"$]+"' "$HERE/installer.nsi" | sed -E 's/File "(.*)"/\1/' | tr '\\' '/'); do
    [ -f "$HERE/$f" ] || { echo "安装脚本引用的文件不存在: $f"; exit 1; }
  done
  [ "$(df --output=avail -BG "$WORK" | tail -1 | tr -dc 0-9)" -ge 8 ] || { echo "$WORK 所在磁盘剩余不足 8G"; exit 1; }
fi
# 版本控制: 包必须对应一个确定的提交 (config.json 是本机私有配置, 不进包也不进 git)
DIRTY=$(git -C "$ROOT" status --porcelain --untracked-files=no -- . ':!config.json' ':!litecodeext/config.json')
[ -z "$DIRTY" ] || { echo "工作区有未提交改动, 先提交再发布:"; echo "$DIRTY"; exit 1; }
# 网页顶栏/健康检查显示的版本号来自 lib/version.py, 必须与发布号一致 (2026-10-22: 2.0.3/2.0.4 都还显示 v2.0.2)
SRC_VER=$(sed -nE 's/^VERSION = "v?([^"]+)"/\1/p' "$ROOT/litecodeext/lib/version.py")
[ "$SRC_VER" = "$VER" ] || { echo "litecodeext/lib/version.py 是 $SRC_VER, 与发布号 $VER 不符: 先改 VERSION 并提交"; exit 1; }
HEAD_SHA=$(git -C "$ROOT" rev-parse HEAD)
if git -C "$ROOT" rev-parse -q --verify "refs/tags/v$VER" >/dev/null; then
  [ "$(git -C "$ROOT" rev-list -n1 "v$VER")" = "$HEAD_SHA" ] || { echo "标签 v$VER 已存在且指向别的提交, 换个版本号"; exit 1; }
fi
echo "  预检通过 (提交 ${HEAD_SHA:0:7})"
[ "${PREFLIGHT_ONLY:-}" = 1 ] && exit 0

step "0b. 发布前测试 (以退出码判定, 任何失败/超时都停止发布)"
if [ "$SKIP_TESTS" = 1 ]; then
  echo "  ⚠⚠⚠ 警告: 已用 --skip-tests 显式跳过发布前测试, 本次发布没有经过测试验证 ⚠⚠⚠"
else
  bash "$ROOT/scripts/run_release_tests.sh" "$ROOT" || { echo "发布前测试未通过, 停止发布 (确需跳过请显式加 --skip-tests)"; exit 1; }
fi

step "1. 公开导出 + 自检"
if [ -f "$ROOT/scripts/make_public_release.sh" ]; then
  bash $ROOT/scripts/make_public_release.sh > "$WORK/public.log" 2>&1 || { tail -20 "$WORK/public.log"; exit 1; }
  grep -q "✓ 自检通过" "$WORK/public.log" || { tail -20 "$WORK/public.log"; echo "公开导出自检未通过, 停止"; exit 1; }
  EXP=$ROOT/dist/litecode-public
else
  # 从公开仓库 clone 下来的代码本身就是导出版 (导出脚本不随公开仓发布), 直接用工作区; 用 config.example.json 做默认配置
  echo "  公开仓库检出, 直接打包当前代码"
  EXP=$ROOT
fi

step "2. 代码更新包"
P=$WORK/codepkg; rm -rf "$P"; mkdir -p "$P/litecode"
cp -a "$EXP/litecodeext/." "$P/litecode/"
cp -a "$EXP/skills_extra/." "$P/litecode/skills/"
cp "$EXP/config.example.json" "$P/litecode/config.default.json"
cp "$EXP/entrypoint.sh" "$P/"
find "$P" -name __pycache__ -prune -exec rm -rf {} +
tar -C "$P" -czf "$OUT/litecode-code-$VER.tar.gz" litecode entrypoint.sh
ls -la "$OUT/litecode-code-$VER.tar.gz" | awk '{printf "  %s  %.1f MB\n", $9, $5/1e6}'

if [ "$FULL" = "--full" ]; then
  step "3. 依赖层 (按指纹缓存)"
  BASE_ID=$(docker image inspect litecode:latest --format '{{.Id}}')
  # 知识库向量模型 (plugins/kb) 打进依赖层; 来源见 scripts/kb_export_onnx.sh
  # 镜像名 v12-r1 → latest 只是改名 (同一个镜像 ID 已计入): 指纹按旧名算, 不让缓存失效、不触发 2GB 完整更新
  FP=$( (echo "$BASE_ID"; sed 's/litecode:latest/litecode:v12-r1/g' "$HERE/Dockerfile.deps"; cat "$HERE"/wsl/*) | sha256sum | cut -c1-12)
  DEPS=$CACHE/deps-$FP.tar.gz
  if [ -f "$DEPS" ]; then
    echo "  命中缓存 deps-$FP ($(cat $CACHE/deps-$FP.version)), 跳过构建和全量扫描"
  else
    echo "  依赖有变化 (指纹 $FP), 构建一次…"
    D=$WORK/deps; rm -rf "$D"; mkdir -p "$D"
    cp "$HERE/Dockerfile.deps" "$D/Dockerfile"; cp "$HERE"/wsl/* "$D/"
    docker image inspect litecode:latest --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -v '^$' | sed 's/^/export /' > "$D/litecode.env"
    docker build -q --build-arg ROOTFS_VERSION="$VER" -t litecode-deps:$FP "$D" >/dev/null
    python3 "$HERE/rootfs.py" deps litecode-deps:$FP "$DEPS"
    echo "$VER" > "$CACHE/deps-$FP.version"
  fi
  ROOTFS_VER=$(cat "$CACHE/deps-$FP.version")

  step "4. 代码层 + 拼接系统包"
  python3 "$HERE/rootfs.py" code "$EXP" "$VER" "$WORK/code-layer.tar.gz"
  python3 "$HERE/rootfs.py" join "$DEPS" "$WORK/code-layer.tar.gz" "$WORK/litecode-rootfs-$ROOTFS_VER.tar.gz"
  python3 "$HERE/rootfs.py" check "$WORK/litecode-rootfs-$ROOTFS_VER.tar.gz"
  cp "$OUT/litecode-code-$VER.tar.gz" "$WORK/"   # 安装包带上代码包: 系统包没变时安装只换代码

  step "5. 托盘程序 + 离线安装包 (含 WSL 官方安装包)"
  WSL_MSI=$CACHE/wsl.3.0.3.0.x64.msi
  [ -f "$WSL_MSI" ] || curl -sSL -m 900 -o "$WSL_MSI" https://github.com/microsoft/WSL/releases/download/3.0.3/wsl.3.0.3.0.x64.msi
  echo "ae977286974255bd26bf925f00f40f5692c6b2084155cbc0563c09aea091f211  $WSL_MSI" | sha256sum -c --quiet
  cp "$WSL_MSI" "$WORK/wsl.msi"
  # 本机操控 (桌面代理) 要的 Python 安装程序 + 依赖 wheel, 随安装包分发, 用户机不用联网下载 (开代理/网差时常失败)
  OFF=$CACHE/win-offline-py313
  if [ ! -f "$OFF/.ok" ]; then
    rm -rf "$OFF"; mkdir -p "$OFF/wheels" "$OFF/sdist"
    curl -fsSL -m 900 -o "$OFF/python-3.13.16-amd64.exe" https://mirrors.huaweicloud.com/python/3.13.16/python-3.13.16-amd64.exe \
      || curl -fsSL -m 900 -o "$OFF/python-3.13.16-amd64.exe" https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe
    PIPI=${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}
    pip3 download -q --platform win_amd64 --python-version 3.13 --implementation cp --only-binary=:all: \
      -d "$OFF/wheels" mss pillow uiautomation pywin32 winocr psutil -i "$PIPI"
    pip3 download -q -d "$OFF/sdist" pyautogui pyperclip -i "$PIPI"
    mv "$OFF"/sdist/*.whl "$OFF/wheels/" 2>/dev/null || true
    for f in "$OFF"/sdist/*.tar.gz; do pip3 wheel -q --no-deps --no-build-isolation -w "$OFF/wheels" "$f"; done
    rm -rf "$OFF/sdist"
    [ -s "$OFF/python-3.13.16-amd64.exe" ] && ls "$OFF/wheels" | grep -qi pyautogui && touch "$OFF/.ok"
  fi
  [ -f "$OFF/.ok" ] || { echo "离线组件准备失败 ($OFF)"; exit 1; }
  rm -rf "$WORK/offline"; cp -a "$OFF" "$WORK/offline"; rm -f "$WORK/offline/.ok"
  (cd "$HERE" && GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath \
     -ldflags "-s -w -H windowsgui -X main.Version=$VER${LITECODE_UPDATE_URL:+ -X main.BaseURL=$LITECODE_UPDATE_URL}" -o "$WORK/LiteCode.exe" .)
  VER4=$(echo "$VER.0.0.0" | cut -d. -f1-4)   # NSIS 文件版本要四段
  # 定制包: LITECODE_PRESET_CONFIG=预置配置 json (模型/能力接入等, 安装时合并), LITECODE_INSTALLER_NAME=安装包文件名前缀
  rm -f "$WORK/preset-config.json"
  [ -n "${LITECODE_PRESET_CONFIG:-}" ] && cp "$LITECODE_PRESET_CONFIG" "$WORK/preset-config.json" && echo "  带预置配置: $LITECODE_PRESET_CONFIG"
  (cd "$HERE" && makensis -V2 -DVER="$VER" -DVER4="$VER4" -DROOTFS_VER="$ROOTFS_VER" -DSRC="$WORK" installer.nsi)
  INST_NAME="${LITECODE_INSTALLER_NAME:-LiteCode-Setup}-$VER.exe"
  mv "$WORK/LiteCode-Setup-$VER.exe" "$OUT/$INST_NAME"
  rm -f "$WORK/preset-config.json"
  echo "$ROOTFS_VER" > "$OUT/.rootfs_version"
  echo "$INST_NAME" > "$OUT/.installer"
  # 中间产物不删 (2026-10-23 用户要求: 缓存包/临时包都保留, 下次打包失败时可复用和排查):
  # 最近一次的系统包和代码层放到 $CACHE/last-build/, 只保留一份, 避免磁盘无限增长
  mkdir -p "$CACHE/last-build"; rm -f "$CACHE"/last-build/*.tar.gz
  mv "$WORK/litecode-rootfs-$ROOTFS_VER.tar.gz" "$WORK/code-layer.tar.gz" "$CACHE/last-build/" 2>/dev/null || true
  echo "  中间产物保留在 $CACHE/last-build/ 和 $WORK/"
fi

step "5b. 模型包 (本地 TTS + 知识库向量模型; 不进安装包, 托盘后台下载 —— windows/models.go)"
TTS_MODEL_SRC=${TTS_MODEL_SRC:-$ROOT/workspace/models/kokoro-multi-lang-v1_1}
KB_MODEL_SRC=${KB_MODEL_SRC:-$ROOT/workspace/kb/models/text2vec-base-chinese-paraphrase-onnx}
if [ -f "$TTS_MODEL_SRC/model.onnx" ] && [ -f "$KB_MODEL_SRC/model.onnx" ]; then
  MSTAGE=$WORK/models; rm -rf "$MSTAGE"; mkdir -p "$MSTAGE"
  cp -al "$TTS_MODEL_SRC" "$MSTAGE/kokoro-multi-lang-v1_1" 2>/dev/null || cp -a "$TTS_MODEL_SRC" "$MSTAGE/kokoro-multi-lang-v1_1"
  cp -al "$KB_MODEL_SRC" "$MSTAGE/text2vec-base-chinese-paraphrase-onnx" 2>/dev/null || cp -a "$KB_MODEL_SRC" "$MSTAGE/text2vec-base-chinese-paraphrase-onnx"
  MTAR=$WORK/models.tar
  tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf "$MTAR" -C "$MSTAGE" .   # 确定性: 内容不变哈希不变
  MSHA=$(sha256sum "$MTAR" | cut -c1-12); MNAME=litecode-models-$MSHA.tar
  if [ -f "$OUT/$MNAME" ]; then echo "  模型包未变: $MNAME"; rm -f "$MTAR"; else mv "$MTAR" "$OUT/$MNAME"; echo "  新模型包: $MNAME $(du -h "$OUT/$MNAME" | cut -f1)"; fi
  echo "$MNAME" > "$OUT/.models"; rm -rf "$MSTAGE"
else
  echo "  ⚠ 缺模型源 ($TTS_MODEL_SRC / $KB_MODEL_SRC), 本次不发模型包 (桌面版配音走在线、知识库不可用)"
fi

step "6. 写 latest.json"
python3 - "$OUT" "$VER" <<'PY'
import hashlib, json, os, sys
out, ver = sys.argv[1], sys.argv[2]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
j = lambda n: os.path.join(out, n)
code = f"litecode-code-{ver}.tar.gz"
r = {"version": ver, "code_file": code, "code_sha256": sha(j(code)), "code_size": os.path.getsize(j(code))}
if os.path.exists(j(".installer")):
    inst = open(j(".installer")).read().strip()
    r.update(rootfs_version=open(j(".rootfs_version")).read().strip(), installer_file=inst,
             installer_sha256=sha(j(inst)), installer_size=os.path.getsize(j(inst)))
if os.path.exists(j(".models")):
    m = open(j(".models")).read().strip()
    if os.path.exists(j(m)):
        r.update(models_file=m, models_sha256=sha(j(m)), models_size=os.path.getsize(j(m)))
json.dump(r, open(j("latest.json"), "w"), indent=1)
print(json.dumps(r, indent=1))
PY
step "7. 打标签并推送 (v$VER → ${HEAD_SHA:0:7})"
git -C "$ROOT" rev-parse -q --verify "refs/tags/v$VER" >/dev/null || \
  git -C "$ROOT" tag -a "v$VER" "$HEAD_SHA" -m "LiteCode v$VER$([ "$FULL" = --full ] && echo ' (含完整安装包)')"
[ "${LITECODE_NO_PUSH:-}" = 1 ] || git -C "$ROOT" push -q origin HEAD:master "v$VER" || echo "  ⚠ 推送失败 (标签已在本地), 网络好了手动: git push origin master v$VER"

step "完成, 总耗时 $(( $(date +%s) - T0 ))s"
[ "$SKIP_TESTS" = 1 ] && echo "  ⚠ 注意: 本次发布跳过了发布前测试 (--skip-tests)"
exit 0
