#!/usr/bin/env python3
"""LiteCode 诊断包一键导出 — stdlib only，把诊断材料打成 zip。"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

LITECODE_HOME = os.environ.get("LITECODE_HOME", "/opt/litecode")
DEFAULT_WORKSPACE = "/tmp/openclaw_workspace"
MAX_SESSION_FILE_MB = 5
SENSITIVE_KEYS = {"api_key", "token", "password"}


# ── Privacy mask ──────────────────────────────────────────────────────────────

def _mask_value(key: str, value):
    if isinstance(value, str) and any(k in key.lower() for k in SENSITIVE_KEYS):
        return "***MASKED***"
    return value


def mask_config(obj, parent_key: str = ""):
    if isinstance(obj, dict):
        return {k: mask_config(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_config(item, parent_key) for item in obj]
    return _mask_value(parent_key, obj)


# ── Collection helpers ────────────────────────────────────────────────────────

def _warn(msg: str, quiet: bool):
    if not quiet:
        print(f"WARN: {msg}", file=sys.stderr)


def _collect_log_tail(log_path: Path, max_mb: int, zf: zipfile.ZipFile, quiet: bool):
    if not log_path.exists():
        _warn(f"trace log not found: {log_path}", quiet)
        return
    size = log_path.stat().st_size
    max_bytes = max_mb * 1024 * 1024
    with open(log_path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            data = f.read()
        else:
            data = f.read()
    zf.writestr("logs/iteration_full.log", data)


def _latest_sessions(sessions_dir: Path, n: int, sid):
    if not sessions_dir.exists():
        return []
    if sid:
        p = sessions_dir / sid
        return [p] if p.exists() else []
    dirs = sorted(
        [d for d in sessions_dir.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return dirs[:n]


def _collect_session(session_dir: Path, zf: zipfile.ZipFile, skipped: list, quiet: bool):
    skip_dirs = {"wechat_creds"}
    skip_exts = {".bin"}
    max_bytes = MAX_SESSION_FILE_MB * 1024 * 1024

    for fpath in session_dir.rglob("*"):
        if not fpath.is_file():
            continue
        if any(part in skip_dirs for part in fpath.parts):
            continue
        if fpath.suffix.lower() in skip_exts:
            continue
        size = fpath.stat().st_size
        if size > max_bytes:
            skipped.append(str(fpath))
            _warn(f"skipping large file ({size // (1024*1024)} MB): {fpath}", quiet)
            continue
        arc_name = "sessions/" + str(fpath.relative_to(session_dir.parent))
        zf.write(fpath, arc_name)


def _collect_telemetry(telemetry_dir: Path, zf: zipfile.ZipFile, quiet: bool):
    if not telemetry_dir.exists():
        _warn(f"telemetry dir not found: {telemetry_dir}", quiet)
        return
    for jsonl in sorted(telemetry_dir.glob("*.jsonl")):
        zf.write(jsonl, f"telemetry/{jsonl.name}")


def _collect_config(config_path: Path, zf: zipfile.ZipFile, quiet: bool):
    if not config_path.exists():
        _warn(f"config.json not found: {config_path}", quiet)
        return
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _warn(f"config.json parse error: {e}", quiet)
        return
    masked = mask_config(raw)
    zf.writestr("config.json", json.dumps(masked, indent=2, ensure_ascii=False))


def _collect_preflight(scripts_dir: Path, workspace: str, zf: zipfile.ZipFile, quiet: bool):
    preflight = scripts_dir / "preflight.py"
    if not preflight.exists():
        zf.writestr("preflight.json", json.dumps({"status": "script_error",
                                                   "error": "preflight.py not found"}))
        return
    try:
        result = subprocess.run(
            [sys.executable, str(preflight), "--json", "--workspace", workspace],
            capture_output=True, text=True, timeout=3,
        )
        data = json.loads(result.stdout)
        data["status"] = "ok" if result.returncode in (0, 2) else "script_error"
    except subprocess.TimeoutExpired:
        data = {"status": "script_error", "error": "timeout"}
    except Exception as e:
        data = {"status": "script_error", "error": str(e)}
    zf.writestr("preflight.json", json.dumps(data, indent=2, ensure_ascii=False))


def _collect_system_info(zf: zipfile.ZipFile):
    try:
        uptime_sec = time.time() - os.stat("/proc/1").st_ctime
    except Exception:
        uptime_sec = None

    info = {
        "platform": platform.platform(),
        "python": sys.version,
        "cwd": os.getcwd(),
        "uptime_sec": round(uptime_sec, 1) if uptime_sec is not None else None,
        "hostname": platform.node(),
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }
    zf.writestr("system_info.json", json.dumps(info, indent=2, ensure_ascii=False))


def _collect_reports(reports_dir: Path, n: int, zf: zipfile.ZipFile, quiet: bool):
    if not reports_dir.exists():
        _warn(f"reports dir not found: {reports_dir}", quiet)
        return
    mds = sorted(reports_dir.glob("iter_*.md"),
                 key=lambda p: p.stat().st_mtime, reverse=True)
    for md in mds[:n]:
        zf.write(md, f"reports/{md.name}")


def _write_readme(zf: zipfile.ZipFile, skipped: list, file_count: int):
    lines = [
        "LiteCode Diagnostic Pack",
        "========================",
        "",
        "Generated: " + datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "",
        "Directory Structure",
        "-------------------",
        "  logs/iteration_full.log   -- tail of iteration trace log",
        "  sessions/                 -- session data (memory, tool_calls, etc.)",
        "  telemetry/*.jsonl         -- telemetry records",
        "  config.json               -- project config (api_key/token masked)",
        "  preflight.json            -- environment self-check result",
        "  system_info.json          -- platform / python / uptime",
        "  reports/iter_*.md         -- recent iteration reports",
        "  README.txt                -- this file",
        "",
    ]
    if skipped:
        lines.append("Skipped Files (> 5 MB or binary)")
        lines.append("---------------------------------")
        for s in skipped:
            lines.append(f"  {s}")
        lines.append("")
    lines.append(f"Total files packed: {file_count}")
    zf.writestr("README.txt", "\n".join(lines))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_output = f"/tmp/litecode_diag_{ts}.zip"

    p = argparse.ArgumentParser(
        description="LiteCode diagnostic pack -- bundle trace, session, telemetry into a zip.",
    )
    p.add_argument("--output", default=default_output,
                   help=f"Output zip path (default: /tmp/litecode_diag_YYYYMMDD_HHMMSS.zip)")
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE,
                   help=f"Workspace path (default: {DEFAULT_WORKSPACE})")
    p.add_argument("--session", default=None,
                   help="Specific session ID to include (default: latest)")
    p.add_argument("--n-sessions", type=int, default=1,
                   help="Number of latest sessions to include (default: 1)")
    p.add_argument("--max-log-mb", type=int, default=5,
                   help="Max tail size of trace log in MB (default: 5)")
    p.add_argument("--include-config", action="store_true", default=True,
                   help="Include config.json (masked) -- default on")
    p.add_argument("--no-config", action="store_true",
                   help="Exclude config.json")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress informational output")
    args = p.parse_args()

    workspace = Path(args.workspace)
    base = Path(LITECODE_HOME)
    scripts_dir = base / "scripts"
    skipped = []

    if not workspace.exists():
        _warn(f"workspace does not exist: {workspace}", args.quiet)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _collect_log_tail(
            workspace / "logs" / "iteration_full.log",
            args.max_log_mb, zf, args.quiet,
        )

        session_dirs = _latest_sessions(
            workspace / "sessions", args.n_sessions, args.session,
        )
        if not session_dirs:
            _warn("no session directories found", args.quiet)
        for sd in session_dirs:
            _collect_session(sd, zf, skipped, args.quiet)

        _collect_telemetry(workspace / "telemetry", zf, args.quiet)

        if args.include_config and not args.no_config:
            _collect_config(base / "config.json", zf, args.quiet)

        _collect_preflight(scripts_dir, str(workspace), zf, args.quiet)

        _collect_system_info(zf)

        _collect_reports(base / "reports", 10, zf, args.quiet)

        names = zf.namelist()
        _write_readme(zf, skipped, len(names) + 1)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    file_count = len(zipfile.ZipFile(out_path).namelist())
    if not args.quiet:
        print(f"Diagnostic pack written: {out_path} ({size_mb:.1f} MB, {file_count} files)")


if __name__ == "__main__":
    main()
