#!/usr/bin/env python3
"""LiteCode 启动环境自检 — 15 项检查，stdlib only，优雅降级。"""

import argparse
import glob
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LITECODE_HOME = os.environ.get("LITECODE_HOME", "/opt/litecode")
DEFAULT_WORKSPACE = "/tmp/openclaw_workspace"
CONFIG_PATH = os.path.join(LITECODE_HOME, "config.json")

COLORS = {
    "OK":    "\033[32m",
    "WARN":  "\033[33m",
    "FAIL":  "\033[31m",
    "INFO":  "\033[36m",
    "RESET": "\033[0m",
}


@dataclass
class Check:
    name: str
    status: str  # OK / WARN / FAIL / INFO
    message: str = ""
    detail: str = ""

    def as_dict(self):
        d = {"name": self.name, "status": self.status, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        return d


# ── Individual check functions ────────────────────────────────────────────────

def check_python_version() -> Check:
    vi = sys.version_info
    ver = f"{vi.major}.{vi.minor}.{vi.micro}"
    if vi >= (3, 10):
        return Check("python_version", "OK", f"Python {ver}")
    return Check("python_version", "FAIL", f"Python {ver} < 3.10 (required)")


def check_stdlib_modules() -> Check:
    required = [
        "asyncio", "json", "os", "pathlib", "socket",
        "subprocess", "sys", "importlib", "shutil", "logging",
    ]
    missing = [m for m in required if importlib.util.find_spec(m) is None]
    if missing:
        return Check("stdlib_modules", "FAIL", f"Missing stdlib: {', '.join(missing)}")
    return Check("stdlib_modules", "OK", f"All {len(required)} stdlib modules present")


def check_module_optional(name: str, fail_status: str = "WARN") -> Check:
    spec = importlib.util.find_spec(name)
    if spec is not None:
        return Check(f"module_{name}", "OK", f"{name} importable")
    return Check(f"module_{name}", fail_status, f"{name} not installed")


def check_fastapi_uvicorn() -> Check:
    fa = importlib.util.find_spec("fastapi")
    uv = importlib.util.find_spec("uvicorn")
    missing = [m for m, s in [("fastapi", fa), ("uvicorn", uv)] if s is None]
    if missing:
        return Check("fastapi_uvicorn", "FAIL", f"Missing: {', '.join(missing)}")
    return Check("fastapi_uvicorn", "OK", "fastapi + uvicorn importable")


def check_playwright_chromium() -> Check:
    patterns = [
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
        "/home/*/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return Check("playwright_chromium", "OK", f"Chromium binary: {matches[0]}")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and "chromium" in result.stdout.lower():
            return Check("playwright_chromium", "OK", "playwright dry-run OK")
    except Exception:
        pass

    return Check("playwright_chromium", "WARN",
                 "Chromium binary not found — browser tools unavailable")


def check_xvfb() -> Check:
    path = shutil.which("Xvfb")
    if path:
        return Check("xvfb", "OK", f"Xvfb at {path}")
    return Check("xvfb", "WARN", "Xvfb not found — headless browser may fail")


def check_display() -> Check:
    display = os.environ.get("DISPLAY", "")
    if display:
        return Check("display_env", "INFO", f"DISPLAY={display}")
    return Check("display_env", "INFO", "DISPLAY not set (headless OK if Xvfb available)")


def check_disk(path: str, min_gb: float = 1.0) -> Check:
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        msg = f"{free_gb:.1f} GB free at {path}"
        if free_gb >= min_gb * 2:
            return Check("disk_space", "OK", msg)
        if free_gb >= min_gb:
            return Check("disk_space", "WARN", f"{msg} (< {min_gb*2:.0f} GB, getting low)")
        return Check("disk_space", "FAIL", f"{msg} (< {min_gb:.0f} GB required)")
    except OSError as e:
        return Check("disk_space", "FAIL", f"Cannot stat {path}: {e}")


def check_writable(path: str) -> Check:
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix="preflight_", delete=True):
            pass
        return Check("workspace_writable", "OK", f"{path} is writable")
    except OSError as e:
        return Check("workspace_writable", "FAIL", f"{path} not writable: {e}")


def check_config_json(config_path: str) -> tuple:
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        return Check("config_json", "OK", f"{config_path} valid JSON"), cfg
    except FileNotFoundError:
        return Check("config_json", "FAIL", f"{config_path} not found"), {}
    except json.JSONDecodeError as e:
        return Check("config_json", "FAIL", f"{config_path} invalid JSON: {e}"), {}


def check_endpoint(label: str, host: str, port: int, timeout: float = 3.0) -> Check:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        rc = sock.connect_ex((host, port))
        sock.close()
        if rc == 0:
            return Check(f"endpoint_{label}", "OK", f"TCP {host}:{port} reachable")
        return Check(f"endpoint_{label}", "WARN",
                     f"TCP {host}:{port} unreachable (errno {rc})")
    except OSError as e:
        return Check(f"endpoint_{label}", "WARN", f"TCP {host}:{port} error: {e}")


def check_env_vars() -> Check:
    vars_status = {}
    for var in ["LITECODE_HOME", "OPENCLAW_URL", "OPENCLAW_TOKEN"]:
        vars_status[var] = "set" if os.environ.get(var) else "unset"
    parts = [f"{k}={v}" for k, v in vars_status.items()]
    return Check("env_vars", "INFO", "; ".join(parts))


def check_telemetry_writable(workspace: str) -> Check:
    telemetry_dir = os.path.join(workspace, "telemetry")
    try:
        os.makedirs(telemetry_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=telemetry_dir, prefix="preflight_", delete=True):
            pass
        return Check("telemetry_writable", "OK", f"{telemetry_dir} writable")
    except OSError as e:
        return Check("telemetry_writable", "WARN", f"{telemetry_dir} not writable: {e}")


# ── Orchestration ─────────────────────────────────────────────────────────────

def _parse_backend_url(cfg: dict) -> tuple:
    url = cfg.get("model", {}).get("backend_url", "")
    if not url:
        return "", 0
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port
    except Exception:
        return "", 0


def run_all_checks(workspace: str, config_path: str) -> list:
    checks = []

    checks.append(check_python_version())
    checks.append(check_stdlib_modules())
    checks.append(check_module_optional("httpx", fail_status="WARN"))
    checks.append(check_module_optional("requests", fail_status="FAIL"))
    checks.append(check_fastapi_uvicorn())
    checks.append(check_module_optional("playwright", fail_status="WARN"))

    if importlib.util.find_spec("playwright") is not None:
        checks.append(check_playwright_chromium())
    else:
        checks.append(Check("playwright_chromium", "WARN",
                            "Skipped — playwright not installed"))

    checks.append(check_xvfb())
    checks.append(check_display())
    checks.append(check_disk(workspace))
    checks.append(check_writable(workspace))

    cfg_check, cfg = check_config_json(config_path)
    checks.append(cfg_check)

    host, port = _parse_backend_url(cfg)
    if host and port:
        checks.append(check_endpoint("model", host, port, timeout=3.0))
    else:
        checks.append(Check("endpoint_model", "WARN",
                            "Cannot determine model endpoint from config"))

    checks.append(check_env_vars())
    checks.append(check_telemetry_writable(workspace))

    return checks


# ── Rendering ─────────────────────────────────────────────────────────────────

def _colorize(status: str, text: str, use_color: bool) -> str:
    if not use_color:
        return text
    c = COLORS.get(status, "")
    reset = COLORS["RESET"]
    return f"{c}{text}{reset}"


def render_text(checks: list, quiet: bool = False, use_color: bool = True) -> str:
    lines = []
    if not quiet:
        lines.append("LiteCode Preflight Check")
        lines.append("=" * 52)

    for c in checks:
        if quiet and c.status in ("OK", "INFO"):
            continue
        status_str = _colorize(c.status, f"[{c.status:<4}]", use_color)
        name_col = c.name.ljust(24)
        lines.append(f"  {status_str}  {name_col}  {c.message}")
        if c.detail:
            lines.append(f"           {' ' * 24}  {c.detail}")

    if not quiet:
        lines.append("=" * 52)
        counts = {}
        for c in checks:
            counts[c.status] = counts.get(c.status, 0) + 1
        summary_parts = [f"{s}:{counts[s]}" for s in ["OK", "WARN", "FAIL", "INFO"] if s in counts]
        lines.append("  " + "  ".join(summary_parts))

    return "\n".join(lines)


def render_json(checks: list) -> str:
    counts = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    return json.dumps({
        "checks": [c.as_dict() for c in checks],
        "summary": counts,
        "fail_count": counts.get("FAIL", 0),
        "warn_count": counts.get("WARN", 0),
    }, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="LiteCode preflight: check environment before startup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  all OK (or INFO/WARN only)\n"
            "  1  any FAIL\n"
            "  2  any WARN with --strict\n"
        ),
    )
    p.add_argument("--quiet", action="store_true",
                   help="Only show FAIL / WARN lines")
    p.add_argument("--strict", action="store_true",
                   help="Exit 2 if any WARN (CI mode)")
    p.add_argument("--json", action="store_true",
                   help="Output JSON instead of text")
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE,
                   help=f"Workspace path (default: {DEFAULT_WORKSPACE})")
    p.add_argument("--config", default=CONFIG_PATH,
                   help=f"config.json path (default: {CONFIG_PATH})")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI colors in text output")
    args = p.parse_args()

    checks = run_all_checks(args.workspace, args.config)

    use_color = not args.no_color and sys.stdout.isatty()

    if args.json:
        print(render_json(checks))
    else:
        output = render_text(checks, quiet=args.quiet, use_color=use_color)
        if output:
            print(output)

    fail_count = sum(1 for c in checks if c.status == "FAIL")
    warn_count = sum(1 for c in checks if c.status == "WARN")

    if fail_count > 0:
        sys.exit(1)
    if args.strict and warn_count > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
