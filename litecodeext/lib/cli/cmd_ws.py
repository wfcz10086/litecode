"""ws 子命令组 + preview: workspace 文件浏览与绝对路径预览 (从 litecli.py 拆出)."""
from pathlib import Path
from typing import Optional

import click

from .common import _err, _ok, _info, _req, _r_json
from . import common as _common  # WEB_URL 实时读取: --url 覆盖只改 common 一处
from .common import _json_out, _none_if_empty


# ── ws (workspace) ─────────────────────────────────────────
@click.group()
def ws():
    """Workspace 文件浏览."""


@ws.command("ls")
@click.argument("path", default="")
def ws_ls(path: str):
    d = _r_json("GET", "/api/workspace/files", params={"path": path})
    if _json_out(d):
        return
    for f in _none_if_empty(d.get("files", [])):
        icon = "📁" if f["is_dir"] else "📄"
        size = f"{f['size']:>10}" if not f["is_dir"] else " " * 10
        click.echo(f"  {icon}  {size}  {f['name']}")


@ws.command("cat")
@click.argument("path")
def ws_cat(path: str):
    r = _req("GET", "/api/workspace/preview", params={"path": path})
    if r.status_code != 200:
        _err(f"HTTP {r.status_code}")
    ct = r.headers.get("content-type", "")
    if "json" in ct:
        d = r.json()
        if d.get("type") == "text":
            click.echo(d.get("content", ""))
        else:
            _info(f"{d.get('type', '?')} 文件: {path} ({d.get('size', '?')} bytes)")
    else:
        # 二进制 (image/pdf) — 不输出，提示用 download
        _info(f"二进制 {ct}, 用 'litecli ws download {path}' 下载查看")


@ws.command("download")
@click.argument("path")
@click.option("-o", "--output", default=None)
def ws_download(path: str, output: Optional[str]):
    # [large-file 2026-10-21] 原来 r.content 整份进内存; 改走流式 + .part 原子改名
    from .cmd_files import _fetch_to
    _fetch_to("/api/workspace/download", {"path": path}, path, Path(path).name, output)


# ── preview (绝对路径文件) ──────────────────────────────────
@click.command()
@click.argument("path")
def preview(path: str):
    """预览绝对路径文件 (用 /api/preview 的白名单端点)."""
    r = _req("GET", "/api/preview", params={"path": path})
    if r.status_code != 200:
        _err(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    typ = d.get("type", "?")
    if typ == "text":
        click.echo(d.get("content", ""))
    else:
        _info(f"{typ}: {d.get('name', '')} ({d.get('size', '?')} bytes)")
        _info(f"  用 litecli ws download / 浏览器打开 {_common.WEB_URL}/api/media?path={path}")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [ws, preview]
