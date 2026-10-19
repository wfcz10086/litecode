"""
LiteCode executor.py v4 — 7个BUG全部修复版
─────────────────────────────────────────────
BUG-1 FIX: apt/dpkg exit=0 误判 → 检测 stdout 中 "E:" / "Unable to fetch"
BUG-2 FIX: timeout=30 装包超时 → 命令类型自动推断 timeout
BUG-3 FIX: systemctl 无 fallback → init系统检测 + fallback链
BUG-4 FIX: probe-first原则 → 网络命令自动前置 probe
BUG-5 FIX: pip PEP668 → 自动追加 --break-system-packages
BUG-6 FIX: 永久错误走指数退避 → 错误分类器
BUG-7 FIX: dpkg锁竞争 → asyncio.Lock 序列化包管理命令
"""

import os, re, signal, subprocess, asyncio, time, shutil
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────
# BUG-6 FIX: 错误分类器
# ─────────────────────────────────────────────────────────
PERMANENT_ERRORS = [
    "Name or service not known",
    "Temporary failure in name resolution",
    "Could not resolve host",
    "Connection refused",
    "No route to host",
    "Permission denied",
    "command not found",
    "No such file or directory",
    "dpkg: error",
    "E: Unable to fetch",
    "E: Failed to fetch",
    "externally-managed-environment",
    # curl 特有错误文本（比 exit code 更精确）
    "curl: (7)",   # curl: Failed to connect
    "curl: (6)",   # curl: Could not resolve host
    "Failed to connect to",
]

RETRYABLE_ERRORS = [
    "timeout",
    "Timeout",
    "TIMEOUT",
    "429",
    "503",
    "502",
    "Connection reset",
    "Broken pipe",
]

def classify_error(output: str, returncode: int, command: str = "") -> str:
    """
    返回: 'ok' | 'retryable' | 'permanent' | 'test_fail' | 'failed'

    关键修复: returncode==0 直接返回 'ok'，不检查 output 中的错误模式。
    [v7] 新增 'test_fail': 断言/测试失败不重试, 直接返回给 LLM 分析。
    [v8] 新增: 负 exit code（信号终止）对 kill/pkill/fuser 命令视为成功。
    """
    if returncode == 0:
        return 'ok'
    # [v8] 负 exit code = 进程被信号终止。对于 kill/pkill/fuser 等进程管理命令，
    # 这是正常的（目标进程已被杀死或命令自身被信号中断），不应重试。
    if returncode < 0:
        # [2026-10-19] 只有 kill 类命令被信号打断才算正常; 别的命令被 SIGSEGV/OOM 杀掉原来也判 ok,
        # 残缺输出当成功结果交给模型
        return 'ok' if re.search(r"\b(kill|pkill|killall|fuser)\b", command or "") else 'failed'
    # [v7] 测试/断言失败: 不是基础设施错误, 不需要重试
    _TEST_FAIL_PATTERNS = [
        'AssertionError', 'assert ', 'FAILED', 'FAIL',
        'Error: expect', 'Expected', 'test failed',
    ]
    for pat in _TEST_FAIL_PATTERNS:
        if pat in output:
            return 'test_fail'
    for pat in PERMANENT_ERRORS:
        if pat in output:
            return 'permanent'
    for pat in RETRYABLE_ERRORS:
        if pat in output:
            return 'retryable'
    # [2026-10-13] 原来兜底也是 retryable: 复现脚本抛异常 / grep 无匹配 / 普通非 0 退出都被自动重跑 3 次,
    # 改文件的命令跑 3 遍有副作用, 还白等 3 秒。没认出来的非 0 退出原样交给模型判断。
    return 'failed'


# 只有这类命令碰到网络抖动才值得自动重试; 别的命令输出里出现 "502"/"timeout" 字样多半只是内容
_NETWORK_CMD = re.compile(
    r"\b(curl|wget|pip3?|uv|npm|pnpm|yarn|apt(-get)?|git\s+(clone|fetch|pull|push)|docker\s+(pull|push)|ssh|scp|rsync)\b")


_NON_IDEMPOTENT = re.compile(r"\bssh\b(?!-)|\bcurl\b[^|;&]*\s(-X\s*(POST|PUT|PATCH|DELETE)|-d\b|--data\S*|-F\b|--form\b|--upload-file\b|-T\b)")


def is_network_cmd(command: str) -> bool:
    """会自动重试的网络命令。[2026-10-19] ssh 远程跑的可能是迁移/部署脚本, POST/上传类 curl
    有副作用 —— 输出里碰巧带 502/timeout 就重跑 3 遍会重复执行, 这两类不自动重试。"""
    c = command or ""
    return bool(_NETWORK_CMD.search(c)) and not _NON_IDEMPOTENT.search(c)


# ─────────────────────────────────────────────────────────
# pgrep -f / pkill -f 自匹配: 模式串原样出现在 bash -c 的命令行里, pgrep 总能找到"自己"
# (2026-10-19 视频任务: `until ! pgrep -f "run.py --from check"; do sleep 10; done` 等满 900s 超时,
#  `pgrep -f run.py | wc -l` 永远报 1 = "还在跑"; pkill -f 更糟, 会把执行它的 shell 一起杀掉)
# 经典解法: 模式首字符包进方括号 "[r]un.py" —— 正则含义不变, 但命令行里的字面串不再匹配它。
# ─────────────────────────────────────────────────────────
_SELF_MATCH = re.compile(r"""\b(pgrep|pkill)((?:\s+-[A-Za-z]+)*?\s+-[A-Za-z]*f[A-Za-z]*(?:\s+-[A-Za-z]+)*)\s+(?:"([^"]+)"|'([^']+)'|([^\s;|&)'"]+))""")


def fix_self_match(command: str) -> tuple:
    """→ (改写后的命令, 说明 or "")"""
    changed = []

    def _sub(m):
        pat = m.group(3) or m.group(4) or m.group(5)
        if "[" in pat:
            return m.group(0)
        def _br(alt):
            i = next((k for k, ch in enumerate(alt) if ch.isalnum()), None)
            return alt if i is None else alt[:i] + "[" + alt[i] + "]" + alt[i + 1:]
        new = "|".join(_br(a) for a in pat.split("|"))     # 每个分支都要包, "render.py|run.py" 只包第一个仍会自匹配
        if new == pat:
            return m.group(0)
        q = '"' if m.group(3) is not None else ("'" if m.group(4) is not None else "'")
        changed.append(f"{pat} → {new}")
        return f"{m.group(1)}{m.group(2)} {q}{new}{q}"

    out = _SELF_MATCH.sub(_sub, command)
    note = ("[已自动改写 pgrep/pkill -f 模式, 防止匹配到命令自己: " + "; ".join(changed) +
            "。等后台任务结束更稳的办法: grep 日志里的 [BG-EXIT code=N]]\n") if changed else ""
    return out, note


# ─────────────────────────────────────────────────────────
# BUG-2 FIX: 命令类型自动推断 timeout
# ─────────────────────────────────────────────────────────
def infer_timeout(command: str, user_timeout: int = None) -> Optional[int]:
    """
    Returns None for long-lived services (no timeout),
    or int seconds for everything else.
    """
    if user_timeout is not None:
        return None if user_timeout == 0 else user_timeout
    cmd = command.strip().lower()
    # [2026-10-19] 前台 `sleep 45; tail log` 没写 timeout → 默认 30s 把 sleep 自己杀了, 什么都没看到
    _sl = sum(float(x) for x in re.findall(r"\bsleep\s+(\d+(?:\.\d+)?)\b", cmd))
    if _sl >= 25:
        return int(min(600, _sl + 30))
    # Long-lived services -- never time out (caller should use background=True)
    if any(k in cmd for k in [
        'uvicorn ', 'gunicorn ', 'flask run', 'fastapi run',
        'node ', 'nodemon ', 'npm start', 'yarn start',
        'streamlit run', 'jupyter ', 'tornado', 'aiohttp',
        'python -m http', 'python3 -m http',
    ]):
        return None
    # Package installs
    if any(k in cmd for k in ['apt-get install','apt install','pip install',
                                'npm install','yarn install','cargo build',
                                'go build','docker pull','docker build']):
        return 300
    # Network requests
    if any(k in cmd for k in ['curl ','wget ','git clone','git pull','git fetch']):
        return 60
    # Tests / compile
    if any(k in cmd for k in ['pytest','npm test','make ','cmake','gcc ','g++ ']):
        return 120
    return 30


# ─────────────────────────────────────────────────────────
# BUG-3 FIX: init系统检测
# ─────────────────────────────────────────────────────────
def detect_init_system() -> str:
    """返回: 'systemd' | 'sysv' | 'runit' | 'none'"""
    try:
        r = subprocess.run(['ps', '-p', '1', '-o', 'comm='],
                           capture_output=True, text=True, timeout=3)
        p1 = r.stdout.strip()
        if 'systemd' in p1:
            return 'systemd'
        if p1 in ('init', 'sysvinit'):
            return 'sysv'
        if p1 == 'runit':
            return 'runit'
    except Exception:
        pass
    # fallback: 检测 systemctl 可用性
    if shutil.which('systemctl'):
        r = subprocess.run(['systemctl', 'status'], capture_output=True, timeout=3)
        if r.returncode != 1:   # 1 = degraded but alive
            return 'none'
        return 'systemd'
    return 'none'

_INIT_SYSTEM = None

def get_init_system() -> str:
    global _INIT_SYSTEM
    if _INIT_SYSTEM is None:
        _INIT_SYSTEM = detect_init_system()
    return _INIT_SYSTEM


def service_start_cmd(service: str) -> str:
    """BUG-3: 根据 init 系统生成正确的启动命令"""
    init = get_init_system()
    if init == 'systemd':
        return f'systemctl start {service}'
    elif init == 'sysv':
        return f'service {service} start'
    else:
        # 容器环境：直接启动守护进程
        daemon_map = {
            'docker': 'dockerd --host unix:///var/run/docker.sock > /tmp/dockerd.log 2>&1 &',
            'nginx':  'nginx',
            'redis':  'redis-server --daemonize yes',
        }
        return daemon_map.get(service, f'{service} &')


# ─────────────────────────────────────────────────────────
# BUG-7 FIX: 包管理互斥锁
# ─────────────────────────────────────────────────────────
_PKG_LOCK = asyncio.Lock()

PKG_COMMANDS = ['apt-get', 'apt ', 'dpkg', 'pip ', 'pip3', 'npm ', 'yarn ', 'brew ']

def is_pkg_command(cmd: str) -> bool:
    return any(k in cmd for k in PKG_COMMANDS)


# ─────────────────────────────────────────────────────────
# BUG-3 FIX: init系统检测（修正版）
# ─────────────────────────────────────────────────────────
def detect_init_system() -> str:
    """精确检测：PID1名称 + systemctl可用性双验证"""
    try:
        r = subprocess.run(['ps', '-p', '1', '-o', 'comm='],
                           capture_output=True, text=True, timeout=3)
        p1 = r.stdout.strip()
        if p1 == 'systemd':
            # 双重验证：systemctl is-system-running 状态
            r2 = subprocess.run(['systemctl', 'is-system-running'],
                                capture_output=True, text=True, timeout=3)
            if r2.stdout.strip() in ('running', 'degraded'):
                return 'systemd'
    except Exception:
        pass
    # 检查 /run/systemd/private（容器内即使有systemctl也可能是fake）
    if Path('/run/systemd/private').exists():
        return 'systemd'
    if shutil.which('service'):
        return 'sysv'
    return 'none'


# ─────────────────────────────────────────────────────────
# BUG-4 FIX: 网络 probe 缓存（BUG-8修正：跳过本地地址）
# ─────────────────────────────────────────────────────────
_NET_PROBE_CACHE: dict = {}

_LOCAL_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}

def probe_host(host: str, timeout: int = 3, scheme: str = "https") -> bool:
    """BUG-8: 本地地址直接放行，不走网络probe
    [2026-10-19] 原来不管命令写的是 http://x:54321 都去 curl https://x (443), 443 没开就判"不可达",
    结果还永久缓存 —— 整个进程里访问这台机器的命令全被拦。改成对命令里真实的 host:port 做 TCP 连接,
    不通只缓存 30 秒 (通了缓存 10 分钟)。"""
    bare, _, port = host.rpartition(':') if host.count(':') == 1 else (host, '', '')
    if not bare:
        bare, port = host, ''
    if bare in _LOCAL_HOSTS or bare.startswith(('192.168.', '10.', '172.')):
        return True
    port = int(port) if port.isdigit() else (80 if scheme == "http" else 443)
    key = f"{bare}:{port}"
    hit = _NET_PROBE_CACHE.get(key)
    if hit and time.time() - hit[1] < (600 if hit[0] else 30):
        return hit[0]
    try:
        import socket
        with socket.create_connection((bare, port), timeout=timeout):
            ok = True
    except OSError:
        ok = False
    _NET_PROBE_CACHE[key] = (ok, time.time())
    return ok

def extract_host(command: str):
    m = re.search(r'https?://([^/\s"\']+)', command)
    return m.group(1) if m else None


def extract_scheme(command: str) -> str:
    m = re.search(r'(https?)://', command)
    return m.group(1) if m else "https"


# ─────────────────────────────────────────────────────────
# BUG-5 FIX: pip 自动追加 --break-system-packages
# ─────────────────────────────────────────────────────────
def patch_pip_cmd(command: str) -> str:
    if re.search(r'\bpip3?\s+install\b', command):
        if '--break-system-packages' not in command:
            # 检测是否在 venv 里（venv 不需要这个 flag）
            in_venv = bool(os.environ.get('VIRTUAL_ENV') or os.environ.get('CONDA_DEFAULT_ENV'))
            if not in_venv:
                command = command.rstrip() + ' --break-system-packages'
    return command


# ─────────────────────────────────────────────────────────
# BUG-1 FIX: apt/dpkg 成功检测
# ─────────────────────────────────────────────────────────
APT_FAIL_PATTERNS = [
    r'^E: ',
    r'Unable to fetch',
    r'Failed to fetch',
    r'dpkg: error',
    r'Sub-process.*returned an error',
    r'unmet dependencies',
]

def apt_succeeded(output: str, returncode: int) -> bool:
    """apt-get 可能 exit=0 但实际失败，需要检查 stdout"""
    if returncode != 0:
        return False
    for pat in APT_FAIL_PATTERNS:
        if re.search(pat, output, re.MULTILINE):
            return False
    return True


# ─────────────────────────────────────────────────────────
# 核心 execute_shell（集成所有修复）
# ─────────────────────────────────────────────────────────
MAX_RETRIES  = 3
MAX_OUTPUT   = 4000  # 与 litecode_server.py MAX_SHELL_RESULT 对齐

async def execute_shell_async(
    command: str,
    timeout: int = None,
    cwd: str = None,
    workspace: Path = None,
) -> str:
    """
    完整修复版 execute_shell。
    集成: timeout推断/pip修复/网络probe/包管理锁/apt成功检测/错误分类/退避重试
    """
    work_dir = Path(cwd) if cwd else (workspace or Path.home())
    work_dir.mkdir(parents=True, exist_ok=True)

    # BUG-5: pip 命令自动追加 flag
    command = patch_pip_cmd(command)
    command, _sm_note = fix_self_match(command)

    # BUG-2: 自动推断 timeout（None = 不限时）
    effective_timeout = infer_timeout(command, timeout)

    # BUG-4: 网络命令 probe-first
    host = extract_host(command)
    if host and not probe_host(host, scheme=extract_scheme(command)):
        return (
            f"ERROR[PERMANENT]: Host unreachable — {host}\n"
            f"DNS/network probe failed before executing: {command}\n"
            f"Suggestion: check egress firewall or use mock/offline mode"
        )

    # BUG-7: 包管理命令互斥
    if is_pkg_command(command):
        async with _PKG_LOCK:
            out = await _run_cmd(command, effective_timeout, work_dir)
    else:
        out = await _run_cmd(command, effective_timeout, work_dir)
    return _sm_note + out if _sm_note else out


async def _run_cmd(command: str, timeout: Optional[int], work_dir: Path) -> str:
    """实际执行 + 错误分类 + 退避重试"""
    last_result = ""
    delay = 1.0

    for attempt in range(MAX_RETRIES):
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _exec_sync, command, timeout, work_dir)

        output   = result["output"]
        exitcode = result["returncode"]

        # BUG-1: apt 成功检测
        is_apt = any(k in command for k in ['apt-get', 'apt ', 'dpkg'])
        if is_apt and not apt_succeeded(output, exitcode):
            error_class = 'permanent'
        else:
            error_class = classify_error(output, exitcode, command)

        if error_class == 'ok':
            return output or f"(exit 0)"

        if error_class == 'permanent':
            # BUG-6: 永久错误不重试
            return f"ERROR[PERMANENT]: {output.strip()}"

        # [v7] 测试/断言失败: 不重试, 直接返回给 LLM 分析修复
        if error_class == 'test_fail':
            return f"ERROR[TEST_FAIL]: {output.strip()}"

        if error_class == 'failed' or not is_network_cmd(command):
            return f"ERROR[EXIT={exitcode}]: {output.strip()}"

        # retryable — 只有网络类命令指数退避
        last_result = output
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(delay)
            delay *= 2

    return f"ERROR[RETRIED×{MAX_RETRIES}]: {last_result.strip()}"


def _kill_tree(proc: "subprocess.Popen") -> None:
    """把整个进程组连根杀掉 (SIGTERM 宽限后 SIGKILL)。

    [FIX 2026-09-04] 旧实现用 subprocess.run(timeout=...), 超时时只 Popen.kill()
    直接子进程 (bash), 而 start_new_session=True 把孙进程 (paramiko/sftp、
    python deploy.py 等) 放进了独立进程组 —— 组没人杀, 孙进程成孤儿继续跑,
    调用方 communicate() 也可能一直卡. 实测: 一条 python3 deploy.py 的 sftp.put
    卡住, agent 主会话被冻 9 分钟. 这里按进程组 (killpg) 连根收割.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=2)   # 宽限: SIGTERM 后给 2s 自己退, 退了就不必 SIGKILL
            return
        except subprocess.TimeoutExpired:
            continue


def _exec_sync(command: str, timeout: Optional[int], work_dir: Path) -> dict:
    """[2026-10-19] stdout/stderr 写临时文件而不是管道: 命令里 `python app.py &` 这种没重定向的后台子进程
    会一直占着管道, communicate() 等不到 EOF, 白等满超时后还把刚起的服务连进程组一起杀掉, 模型再起一遍。
    写文件就只等 bash 本身退出; 超时时把已有输出的尾巴一起带回去, 模型能看到卡在哪。"""
    import tempfile
    proc = None
    fo = tempfile.TemporaryFile()
    fe = tempfile.TemporaryFile()

    def _read(f):
        f.seek(0)
        return f.read().decode("utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            command, shell=True, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL,
            cwd=str(work_dir),
            env={**os.environ, "WORKSPACE": str(work_dir)},
            # start_new_session: 子进程进入独立 session/进程组 (setsid)
            # — server 收到 SIGINT/SIGTERM 不会传播给 shell 子进程
            # — shell 子进程的所有孙进程也在同一个新进程组里, 超时用 killpg 统一收割
            start_new_session=True,
            # executable=bash: shell=True 默认走 /bin/sh (Ubuntu=dash), LLM 常生成
            # bash 专属语法 (/dev/tcp、[[ ]]、数组), dash 下静默失败但 exit code
            # 仍可能是 0 (子 shell 失败但外层 || 分支吞掉), 导致结果看似正常实则全错
            executable="/bin/bash",
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)                       # ← 连根杀整个进程组, 不留孤儿
            t_label = f"{timeout}s" if timeout else "unlimited"
            partial = (_read(fo) + _read(fe)).strip()
            tail = ("\n--- 超时前最后的输出 ---\n" + partial[-1200:]) if partial else "\n(超时前没有任何输出)"
            return {"output": f"TIMEOUT after {t_label} (进程组已连根终止) — command: {command[:80]}{tail}",
                    "returncode": -1}
        stdout, stderr = _read(fo), _read(fe)
        rc = proc.returncode
        if rc == 0:
            out = (stdout or "").strip()
            err = (stderr or "").strip()
            if err:
                # 命令成功 (exit 0)，但有 stderr 输出（warnings/deprecations 等）
                # 使用特殊前缀让 cli.py 识别并区别显示，同时让 LLM 知道不是错误
                stderr_note = f"[STDERR_WARNINGS - exit 0, command succeeded]:\n{err}"
                output = (out + "\n" + stderr_note) if out else stderr_note
            else:
                output = out or "(exit 0)"
        else:
            # 失败：合并 stdout+stderr，供错误分类器判断
            output = ((stdout or "") + (stderr or "")).strip() or f"(exit {rc})"
        if len(output) > MAX_OUTPUT:
            output = output[:3000] + f"\n...[truncated {len(output)} chars]...\n" + output[-1500:]
        return {"output": output, "returncode": rc}
    except Exception as e:
        if proc is not None:
            _kill_tree(proc)       # 异常路径也别留活着的子进程组
        return {"output": f"EXEC_ERROR: {e}", "returncode": -1}
    finally:
        fo.close()
        fe.close()


# ─────────────────────────────────────────────────────────
# BUG-3 FIX: Docker 安装+启动完整流程
# ─────────────────────────────────────────────────────────
async def install_and_start_docker(workspace: Path) -> str:
    """
    完整 Docker 安装流程，含 init 系统检测和 dpkg 锁检测。
    """
    steps = []

    # 1. 检测是否已安装
    r = await execute_shell_async("docker --version", workspace=workspace)
    if "Docker version" in r:
        steps.append("✓ Docker already installed: " + r.strip())
    else:
        # 2. 检测 dpkg 锁
        lock = await execute_shell_async(
            "fuser /var/lib/dpkg/lock-frontend 2>/dev/null && echo LOCKED || echo FREE",
            workspace=workspace
        )
        if "LOCKED" in lock:
            steps.append("ERROR[PERMANENT]: dpkg lock held by another process. Run: kill $(fuser /var/lib/dpkg/lock-frontend)")
            return "\n".join(steps)

        # 3. 安装（timeout=300）
        r = await execute_shell_async(
            "apt-get install -y docker.io", timeout=300, workspace=workspace
        )
        steps.append(f"apt-get install: {r[:200]}")
        if r.startswith("ERROR"):
            return "\n".join(steps)

    # 4. 检测 init 系统，选择正确启动方式
    init = get_init_system()
    steps.append(f"init system: {init}")

    start_cmd = service_start_cmd('docker')
    r = await execute_shell_async(start_cmd, workspace=workspace)
    steps.append(f"start docker ({start_cmd}): {r[:200]}")

    # 5. 等待 socket 就绪
    for i in range(5):
        await asyncio.sleep(1)
        sock = Path("/var/run/docker.sock")
        if sock.exists():
            steps.append("✓ /var/run/docker.sock ready")
            break
    else:
        steps.append("⚠ docker socket not ready after 5s")

    return "\n".join(steps)
