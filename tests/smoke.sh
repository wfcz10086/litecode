#!/usr/bin/env bash
# tests/smoke.sh — 30 秒冒烟测试
set -u  # 不用 set -e，遇到 FAIL 继续跑后续检查

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── flags ──
QUIET=0
STRICT=0
for arg in "$@"; do
    case "$arg" in
        -q|--quiet) QUIET=1 ;;
        --strict)   STRICT=1 ;;
        -h|--help)
            echo "Usage: smoke.sh [-q|--quiet] [--strict]"
            echo "  -q, --quiet  Only show FAIL lines"
            echo "  --strict     Exit 1 on any FAIL (same as default; kept for CI consistency)"
            exit 0
            ;;
    esac
done

# ── colors (auto-disable for non-TTY) ──
if [ -t 1 ]; then
    G="\033[32m"; R="\033[31m"; Y="\033[33m"; D="\033[2m"; NC="\033[0m"
else
    G=""; R=""; Y=""; D=""; NC=""
fi

PASS=0; FAIL=0; START_TS=$(date +%s)
declare -a FAILED_NAMES

run_check() {
    local name="$1"; shift
    local out
    out=$("$@" 2>&1)
    local rc=$?
    if [ $rc -eq 0 ]; then
        [ $QUIET -eq 0 ] && printf "${G}[PASS]${NC} %s\n" "$name"
        PASS=$((PASS + 1))
    else
        printf "${R}[FAIL]${NC} %s\n" "$name"
        echo "$out" | head -5 | sed 's/^/    /'
        FAIL=$((FAIL + 1))
        FAILED_NAMES+=("$name")
    fi
}

# ── 1. py_compile ──
# PYTHONPYCACHEPREFIX 重定向字节码到 /tmp, 避免 root 所有的 __pycache__ 权限报错
# 排除 litecodeext/skills/ 下的三方 vendored 脚本 (docx/slack-gif 等)
run_check "1. py_compile (litecodeext + scripts)" bash -c "
    files=\$(find '$ROOT/litecodeext' '$ROOT/scripts' -name '*.py' \
        -not -path '*/litecodeext/skills/*' 2>/dev/null | head -100)
    [ -z \"\$files\" ] && echo 'No .py files found' && exit 1
    PYTHONPYCACHEPREFIX=/tmp/_smoke_pyc python3 -m py_compile \$files
"

# ── 2. import 关键模块 ──
# skills 在 lib.skills, tool_dispatch 在 core.tool_dispatch
# 第三方依赖缺失 (fastapi/uvicorn 等) 算 SKIP 不算 FAIL
run_check "2. import 关键模块" python3 -c "
import sys
sys.path.insert(0, '$ROOT/litecodeext')
sys.path.insert(0, '$ROOT/litecodeext/core')
sys.path.insert(0, '$ROOT/litecodeext/lib')

MODULES = ['cli', 'litecode_server', 'web_ui', 'lib.skills', 'core.tool_dispatch']
THIRD_PARTY_SKIP = ('httpx', 'fastapi', 'playwright', 'starlette', 'uvicorn',
                    'aiofiles', 'websockets', 'openai', 'anthropic')

fail = 0
for m in MODULES:
    try:
        __import__(m)
    except ImportError as e:
        msg = str(e)
        if any(pkg in msg for pkg in THIRD_PARTY_SKIP):
            pass
        else:
            print(f'FAIL: {m}: {e}')
            fail += 1
    except Exception as e:
        print(f'FAIL: {m}: {e}')
        fail += 1

sys.exit(0 if fail == 0 else 1)
"

# ── 3. preflight.py --json 输出合法 JSON ──
run_check "3. preflight.py --json" bash -c "
    out=\$(python3 '$ROOT/scripts/preflight.py' --json 2>&1)
    echo \"\$out\" | python3 -m json.tool > /dev/null
"

# ── 4. analyze_telemetry.py 缺数据不崩 ──
run_check "4. analyze_telemetry.py (缺数据)" bash -c "
    rm -rf /tmp/_smoke_empty_ws
    mkdir -p /tmp/_smoke_empty_ws/telemetry
    python3 '$ROOT/scripts/analyze_telemetry.py' --workspace /tmp/_smoke_empty_ws --since 0 > /dev/null
"

# ── 5. diag_pack.py 生成非空 zip ──
run_check "5. diag_pack.py (生成 zip)" bash -c "
    rm -f /tmp/_smoke_diag.zip
    python3 '$ROOT/scripts/diag_pack.py' \
        --output /tmp/_smoke_diag.zip \
        --workspace /tmp/_smoke_empty_ws \
        --quiet > /dev/null 2>&1
    [ -s /tmp/_smoke_diag.zip ]
"

# ── 6. bash -n 全部 .sh ──
run_check "6. bash -n 全部 .sh" bash -c "
    fail=0
    while IFS= read -r f; do
        bash -n \"\$f\" 2>/tmp/_smoke_bash_n.err || { echo \"syntax error: \$f\"; cat /tmp/_smoke_bash_n.err; fail=1; }
    done < <(find '$ROOT' -name '*.sh' -not -path '*/.git/*' -not -path '*/workspace/*' 2>/dev/null)
    exit \$fail
"

# ── 7. config.json valid JSON ──
run_check "7. config.json valid" bash -c "python3 -m json.tool '$ROOT/config.json' > /dev/null"

# ── 8. docs/SSE_CONTRACT.md 非空 ──
run_check "8. docs/SSE_CONTRACT.md 非空" bash -c "test -s '$ROOT/docs/SSE_CONTRACT.md'"

# ── 9. BACKLOG.md 完成项 commit 一致性 ──
# 每条 "- [x] **ID**" 完成项下方几行内必须留一行真实 "commit: <hash>"（不能是占位符/空）；
# 反之若某条写了 commit 但没勾 [x] 也算不一致。取代老 TODO.md v1.3 分节计数自检（结构已随 BACKLOG.md 废弃）。
run_check "9. BACKLOG.md commit 一致性" python3 -c "
import re, sys
lines = open('$ROOT/BACKLOG.md').read().split('\n')
n = len(lines)

item_re = re.compile(r'^- \[( |x)\] \*\*(\S+?)\*\*')
commit_re = re.compile(r'^\s*commit:\s*(.*)\$', re.I)
placeholders = {'', 'todo', 'tbd', 'xxx', 'n/a', 'placeholder', 'hash', '<hash>'}

errors = []
i = 0
while i < n:
    m = item_re.match(lines[i])
    if not m:
        i += 1
        continue
    checked = m.group(1) == 'x'
    item_id = m.group(2)
    j = i + 1
    commit_val = None
    while j < n and not item_re.match(lines[j]) and not lines[j].startswith('## '):
        if commit_val is None:
            cm = commit_re.match(lines[j])
            if cm:
                commit_val = cm.group(1).strip()
        j += 1
    if checked and (commit_val is None or commit_val.lower() in placeholders):
        errors.append(f'{item_id}: checked [x] but no valid commit hash')
    if not checked and commit_val is not None:
        errors.append(f'{item_id}: has commit line but not checked [x]')
    i = j

if errors:
    for e in errors:
        print(e)
    sys.exit(1)
print('BACKLOG.md commit consistency OK')
"

# ── 10. DAG v2 (Jenkins-lite 3 屏) 资源与入口一致性 ──
# [2026-08-25] 原检查找的是 dag_editor.js + openP('dag'), 那是 dag/v2 重写前的老架构,
# 该文件在 "老 UI 收进 _legacy/ → 彻底删 _legacy/" 两轮重构里已删除, 此检查从那时起一直
# FAIL 但被 CI 吞掉的退出码掩盖 (退出码问题见 P0-3)。改为校验当前真实的 v2 四件套 + 入口。
run_check "10. DAG v2 资源与入口一致性" bash -c "
for f in dag_pipelines.js dag_editor_simple.js dag_build_view.js dag_ui.js; do
    test -f '$ROOT/litecodeext/web_assets/'\$f || { echo \"web_assets/\$f 不存在\"; exit 1; }
    grep -q \"\$f\" '$ROOT/litecodeext/web_ui.html' || { echo \"web_ui.html 缺少 \$f 引用\"; exit 1; }
done
grep -q 'openDAGRouter' '$ROOT/litecodeext/web_ui.html' || { echo 'web_ui.html 缺少 openDAGRouter 入口'; exit 1; }
exit 0
"

# ── 11. wecom + wechat 三端对齐 (核心 CRUD 对称) ──
run_check "11. wecom/wechat 路由对齐" python3 -c "
import re, sys, os
def paths(fp, prefix):
    if not os.path.exists(fp): return set()
    txt = open(fp).read()
    hits = re.findall(r'@router\.\w+\(\"' + re.escape(prefix) + r'([^\"]*)\"', txt)
    return set(hits)
wx = paths('$ROOT/litecodeext/routers/wechat_router.py', '/api/wechat')
wc = paths('$ROOT/litecodeext/routers/wecom_router.py',  '/api/wecom')
core = {'/status', '/config', '/bots', '/bots/{bot_id}',
        '/bots/{bot_id}/relogin', '/bots/{bot_id}/send'}
missing_wc = core - wc
missing_wx = core - wx
if missing_wc: print(f'wecom_router 缺: {sorted(missing_wc)}'); sys.exit(1)
if missing_wx: print(f'wechat_router 缺: {sorted(missing_wx)}'); sys.exit(1)
print(f'align OK: wx={len(wx)} wc={len(wc)} core=6')
"

# ── 12. wecom_bridge 流式 API ──
run_check "12. wecom send_markdown_stream 可用" python3 -c "
import sys, os
sys.path.insert(0, '$ROOT/litecodeext')
try:
    from wecom_bridge import WeComBotInstance
except ImportError as e:
    if any(p in str(e) for p in ('websocket', 'aiohttp', 'aiosqlite')):
        print('SKIP (missing dep):', e); sys.exit(0)
    raise
assert hasattr(WeComBotInstance, 'send_markdown_stream'), 'send_markdown_stream 未定义'
import inspect
sig = inspect.signature(WeComBotInstance.send_markdown_stream)
missing = {'req_id', 'stream_id', 'content', 'finish'} - set(sig.parameters)
assert not missing, f'签名缺参数: {missing}'
print('stream signature OK')
"

# ── 13. tests/ pytest 无新增失败 (2026-08-26 补的验收缺口) ──
# 背景: M1-M5b 七轮里程碑验收跑的是 test_full.py --offline + 本脚本, **没跑 tests/ 下的
# pytest 文件**, 导致 test_reasoning_stall_wording 因行号漂移全线失败七轮无人发现。
# 本检查拿实跑结果跟 tests/pytest_known_failures.txt 比对, **只拦新增失败**
# (那 40 条既有失败是 sys.path/缺辅助模块之类的基建问题, 不该阻塞日常开发)。
run_check "13. tests/ pytest 无新增失败" bash -c "
BASE='$ROOT/tests/pytest_known_failures.txt'
[ -f \"\$BASE\" ] || { echo \"基线文件缺失: \$BASE\"; exit 1; }
ACTUAL=\$(cd '$ROOT' && timeout 300 python3 -m pytest tests/ -q -p no:cacheprovider \
    --ignore=tests/web --ignore=tests/regression 2>/dev/null \
    | grep '^FAILED' | sed 's/^FAILED //; s/ - .*//' | sort)
KNOWN=\$(grep -v '^#' \"\$BASE\" | grep -v '^\$' | sort)
NEW=\$(comm -23 <(echo \"\$ACTUAL\") <(echo \"\$KNOWN\"))
if [ -n \"\$NEW\" ]; then
    echo '新增失败 (不在基线内):'; echo \"\$NEW\" | sed 's/^/    /'; exit 1
fi
echo \"无新增失败 (基线 \$(echo \"\$KNOWN\" | wc -l) 条)\"
exit 0
"

# ── summary ──
ELAPSED=$(($(date +%s) - START_TS))
TOTAL=$((PASS + FAIL))
echo ""
if [ $FAIL -eq 0 ]; then
    printf "${G}[smoke] %d/%d PASS (%ds)${NC}\n" "$PASS" "$TOTAL" "$ELAPSED"
else
    printf "${R}[smoke] %d/%d FAIL (%ds)${NC}\n" "$FAIL" "$TOTAL" "$ELAPSED"
    printf "Failed: %s\n" "${FAILED_NAMES[*]}"
fi

# ── cleanup ──
rm -rf /tmp/_smoke_empty_ws /tmp/_smoke_diag.zip /tmp/_smoke_bash_n.err /tmp/_smoke_pyc 2>/dev/null

[ $FAIL -gt 0 ] && exit 1
exit 0

