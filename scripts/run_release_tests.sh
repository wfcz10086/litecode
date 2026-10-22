#!/usr/bin/env bash
# 发布前测试门禁 (packaging/release.sh 在预检之后调用; 也可单独跑)。
#
#   bash scripts/run_release_tests.sh [仓库根目录]
#
# 每一项都以命令自身 / timeout 的退出码判定: 任何非 0 (含超时 124/137、pytest 收集错误、没收集到用例) 都算失败,
# 不解析 FAILED/ERROR 文本。全部通过退出 0, 否则退出 1 并列出失败项。
# 超时可用环境变量调整: RELEASE_PYTEST_TIMEOUT (默认 900) / RELEASE_SMOKE_TIMEOUT (默认 600) / RELEASE_GO_TIMEOUT (默认 300)
set -uo pipefail
ROOT=$(cd "${1:-$(dirname "$0")/..}" && pwd) || { echo "仓库目录不存在: ${1:-}"; exit 1; }
LOGDIR=$(mktemp -d)
[ -x "$HOME/.local/go/bin/go" ] && export PATH="$HOME/.local/go/bin:$PATH"   # 单独跑时也找得到 go (release.sh 里本来就加了)
FAILED=()

gate() {   # gate <名称> <超时秒> <目录> <命令...>
  local name=$1 limit=$2 dir=$3; shift 3
  local log="$LOGDIR/$(echo "$name" | tr -c 'A-Za-z0-9' '_').log" t0=$SECONDS rc
  echo "  ▶ $name"
  (cd "$dir" && exec timeout -k 10 "$limit" "$@") > "$log" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "    ✓ 通过 ($((SECONDS - t0))s) $(tail -1 "$log")"
    return 0
  fi
  case $rc in
    124|137) echo "    ✗ 超时 (${limit}s, rc=$rc)" ;;
    *)       echo "    ✗ 失败 (rc=$rc)" ;;
  esac
  tail -25 "$log" | sed 's/^/      /'
  FAILED+=("$name")
  echo "$name" >> "$LOGDIR/failed.list"
  return 1
}

[ -d "$ROOT/litecodeext/tests" ] || { echo "  ✗ 找不到 $ROOT/litecodeext/tests"; exit 1; }
# 装了 pytest-xdist 就按 CPU 核数并行 (2026-10-22: 串行约 175s → 8 核约 48s); 没装照旧串行
PYTEST_PAR=()
python3 -c "import xdist" 2>/dev/null && PYTEST_PAR=(-n "${RELEASE_PYTEST_WORKERS:-auto}")
# 三组互不依赖, 并行跑 (各自输出先写文件, 结束后按顺序打印)
# 并行时偶有浏览器类用例抢资源超时 (2026-10-22 doc_studio 的 playwright 用例): 并行失败的用例再串行重跑一次, 仍失败才算失败
PC="-o cache_dir=$LOGDIR/.pytest_cache"
if [ ${#PYTEST_PAR[@]} -gt 0 ]; then
  PYCMD="python3 -m pytest -q $PC ${PYTEST_PAR[*]} tests || { echo '--- 并行失败的用例串行重跑 ---'; python3 -m pytest -q $PC --lf tests; }"
else
  PYCMD="python3 -m pytest -q $PC tests"
fi
gate "litecodeext pytest" "${RELEASE_PYTEST_TIMEOUT:-900}" "$ROOT/litecodeext" bash -c "$PYCMD" > "$LOGDIR/out.1" 2>&1 &
{
if [ -f "$ROOT/tests/smoke.sh" ]; then
  gate "tests/smoke.sh" "${RELEASE_SMOKE_TIMEOUT:-600}" "$ROOT" bash tests/smoke.sh -q
else
  echo "  ✗ 找不到 tests/smoke.sh"; echo "tests/smoke.sh 缺失" >> "$LOGDIR/failed.list"
fi
} > "$LOGDIR/out.2" 2>&1 &
{
if [ -d "$ROOT/packaging/windows" ]; then
  if command -v go >/dev/null; then
    gate "托盘程序 go test" "${RELEASE_GO_TIMEOUT:-300}" "$ROOT/packaging/windows" go test ./upgrade/...
    gate "托盘程序 go vet (windows)" "${RELEASE_GO_TIMEOUT:-300}" "$ROOT/packaging/windows" \
      env GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go vet ./...
  else
    echo "  ✗ 缺工具 go, 托盘程序测试没跑"; echo "go 缺失" >> "$LOGDIR/failed.list"
  fi
  for f in "$ROOT"/packaging/windows/wsl/litecode-*; do
    gate "bash -n $(basename "$f")" 30 "$ROOT" bash -n "$f"
  done
fi
} > "$LOGDIR/out.3" 2>&1 &
wait
cat "$LOGDIR/out.1" "$LOGDIR/out.2" "$LOGDIR/out.3"
[ -f "$LOGDIR/failed.list" ] && mapfile -t FAILED < "$LOGDIR/failed.list"
rm -rf "$LOGDIR"
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "  测试未通过: ${FAILED[*]}"
  exit 1
fi
echo "  全部测试通过"
