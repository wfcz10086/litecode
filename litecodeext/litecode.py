#!/usr/bin/env python3
"""
litecode.py -- LiteCode Python 启动器
1. 安装依赖  2. 杀旧进程  3. 启动 server  4. 可选启动 web UI  5. 运行 REPL
"""
import os, sys, json, time, subprocess, argparse, signal
from pathlib import Path

BASE      = Path(__file__).resolve().parent
LITECODE  = Path.home() / ".litecode"
LITECODE.mkdir(parents=True, exist_ok=True)

# ── Config ────────────────────────────────────────────────────
def _load_cfg() -> dict:
    p = BASE / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}

CFG       = _load_cfg()
PORT      = CFG.get("server", {}).get("port", 18789)
TOKEN     = CFG.get("server", {}).get("token", "CHANGE_ME_TOKEN")
WEB_PORT  = CFG.get("web_ui", {}).get("port", 18790)
# 路径解析：与 litecode_server.py 保持一致
# 优先 paths.workspace_base → 降级 agent.workspace → 默认 ~/.litecode/workspace
_PATHS    = CFG.get("paths", {})
_AGT      = CFG.get("agent", {})
WORKSPACE = Path(_PATHS.get("workspace_base", _AGT.get("workspace", str(LITECODE / "workspace"))))
WORKSPACE.mkdir(parents=True, exist_ok=True)
LOGS_DIR  = Path(_PATHS.get("logs_dir", str(WORKSPACE / "logs")))
LOGS_DIR.mkdir(parents=True, exist_ok=True)
SERVER_LOG = LOGS_DIR / "server.log"

# ── Colors ────────────────────────────────────────────────────
_T  = sys.stdout.isatty()
G   = "\033[0;32m" if _T else ""
Y   = "\033[1;33m" if _T else ""
R   = "\033[0;31m" if _T else ""
C   = "\033[0;36m" if _T else ""
D   = "\033[2m"    if _T else ""
B   = "\033[1m"    if _T else ""
NC  = "\033[0m"    if _T else ""

def _echo(msg): print(f"  {msg}", flush=True)
def _ok(msg):   print(f"  {G}{msg}{NC}", flush=True)
def _warn(msg): print(f"  {Y}{msg}{NC}", flush=True)
def _err(msg):  print(f"  {R}{msg}{NC}", flush=True)

# ── Server health ─────────────────────────────────────────────
def server_alive() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/health",
            headers={"Authorization": f"Bearer {TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status < 500
    except Exception:
        return False

# ── Kill old processes ────────────────────────────────────────
def kill_old():
    """先 SIGTERM 优雅停止，等 1 秒后强杀残留。"""
    for pat in ["litecode_server.py", "web_ui.py"]:
        try:
            subprocess.run(["pkill", "-f", pat], capture_output=True, timeout=5)
        except Exception:
            pass
    time.sleep(1.0)
    # 残留进程强杀
    for pat in ["litecode_server.py", "web_ui.py"]:
        try:
            subprocess.run(["pkill", "-9", "-f", pat], capture_output=True, timeout=3)
        except Exception:
            pass
    time.sleep(0.3)

# ── Show server log tail ──────────────────────────────────────
def show_server_log(lines: int = 25):
    if not SERVER_LOG.exists():
        return
    tail = SERVER_LOG.read_text(errors="replace").splitlines()[-lines:]
    if tail:
        print(f"\n{D}--- {SERVER_LOG} ---{NC}")
        print("\n".join(tail))
        print(f"{D}{'─'*50}{NC}\n")

# ── Bootstrap dependencies ────────────────────────────────────
def bootstrap():
    REQUIRED = ["requests", "httpx", "fastapi", "uvicorn", "pydantic", "prompt_toolkit"]
    missing  = []
    for pkg in REQUIRED:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        _warn(f"Installing: {' '.join(missing)} ...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + missing +
            ["--break-system-packages", "-q"],
            capture_output=True, text=True
        )
        stderr = "\n".join(
            l for l in r.stderr.splitlines()
            if l.strip()
            and "WARNING: Running pip as" not in l
            and "virtual environment" not in l
        )
        if stderr:
            _warn(stderr[:300])

# ── Start server ──────────────────────────────────────────────
def start_server() -> bool:
    server_py = BASE / "litecode_server.py"
    if not server_py.exists():
        _err("litecode_server.py not found")
        return False

    # 用 fd 分离方式避免 log_fd 泄漏：先打开 fd，Popen 继承后立即关闭 Python 侧
    log_fd = open(SERVER_LOG, "a")
    log_fd.write(f"\n{'='*40}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting...\n")
    log_fd.flush()
    try:
        proc = subprocess.Popen(
            [sys.executable, str(server_py)],
            stdout=log_fd, stderr=log_fd,
            start_new_session=True,
        )
    finally:
        log_fd.close()  # Popen 已经 dup 了 fd，这里安全关闭

    _echo(f"{D}Server pid={proc.pid}, waiting...{NC}")
    for i in range(60):
        time.sleep(0.5)
        if proc.poll() is not None:
            _err(f"Server process exited immediately (code={proc.returncode})")
            show_server_log(50)
            return False
        if server_alive():
            return True
        if (i + 1) % 10 == 0:
            _echo(f"{D}  still waiting... {(i+1)*0.5:.0f}s{NC}")

    _err("Server start timeout (30s)")
    show_server_log(50)
    return False

# ── Start web UI ──────────────────────────────────────────────
def start_web(port: int | None = None) -> bool:
    web_py = BASE / "web_ui.py"
    if not web_py.exists():
        _warn("web_ui.py not found, skipping web UI")
        return False
    _port = port or WEB_PORT
    log_fd = open(LOGS_DIR / "webui.log", "a")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(web_py)],
            env={**os.environ, "WEB_PORT": str(_port)},
            stdout=log_fd, stderr=log_fd,
            start_new_session=True,
        )
    finally:
        log_fd.close()  # Popen 已 dup fd
    for _ in range(10):
        time.sleep(0.5)
        if proc.poll() is not None:
            _err(f"Web UI process died (code={proc.returncode})")
            return False
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{_port}/", timeout=1)
            return True
        except Exception:
            pass
    return proc.poll() is None   # alive but slow is OK

# ── Banner ────────────────────────────────────────────────────
def print_banner(online: bool):
    status  = f"{G}online{NC}" if online else f"{R}offline{NC}"
    model   = CFG.get("model", {}).get("id", "openclaw")
    backend = CFG.get("model", {}).get("backend_url", "?")
    print(f"""
{B}{C}  LiteCode v12r1{NC}
  {D}server {NC}{Y}http://127.0.0.1:{PORT}{NC}  {status}
  {D}model  {NC}{Y}{model}{NC}
  {D}backend{NC} {D}{backend}{NC}
  {D}workspace{NC} {D}{WORKSPACE}{NC}
  {D}--web 同时启动 Web UI (:{WEB_PORT}){NC}
  {D}Type /help · Ctrl-D exit{NC}
""")
    if not online:
        _warn(f"Server offline — 用 /restart 重试，或查看: {SERVER_LOG}")
        print()

# ── Main ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="LiteCode launcher")
    ap.add_argument("--query",      "-q", help="Single query (non-interactive)")
    ap.add_argument("--session",    "-s", help="Session ID")
    ap.add_argument("--no-restart", action="store_true",
                    help="Skip server restart if already running")
    ap.add_argument("--web",        action="store_true",
                    help="Also start Web UI")
    args = ap.parse_args()

    bootstrap()

    online = False
    if args.no_restart and server_alive():
        _ok("Server already online")
        online = True
    else:
        _echo("Killing old processes (server + web UI)...")
        kill_old()
        _echo("Starting LiteCode server...")
        online = start_server()
        if online:
            _ok(f"Server ready on port {PORT}")

    if args.web:
        _echo("Starting Web UI...")
        if start_web():
            _ok(f"Web UI: http://127.0.0.1:{WEB_PORT}")
        else:
            _warn(f"Web UI failed (check {WORKSPACE}/webui.log)")

    os.environ["OPENCLAW_URL"]   = f"http://127.0.0.1:{PORT}"
    os.environ["OPENCLAW_TOKEN"] = TOKEN

    sys.path.insert(0, str(BASE))
    import importlib.util
    spec = importlib.util.spec_from_file_location("cli", BASE / "cli.py")
    cli  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    sid = args.session or cli.load_session()

    if args.query:
        if not online:
            _err("Server offline, cannot execute query")
            sys.exit(1)
        cli.stream_chat(args.query, sid)
    else:
        print_banner(online)
        cli.repl(session_id=sid)


if __name__ == "__main__":
    main()
