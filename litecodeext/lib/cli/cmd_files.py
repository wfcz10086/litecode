"""upload / artifacts / media 命令: 上传本地文件到会话、列出/下载/删除产出物、按服务端路径下载文件。

产出物有两套接口:
  - /api/artifacts_v2/*   Web「产出物」面板用的 owner 维度存储 (timer/bg/plugin/DAG 产出) → artifacts list/owners/show/get/rm/purge
  - /api/artifacts/{sid}  老的会话维度文件记录 (agent 写过哪些文件)            → artifacts session
"""
import sys
import time
from pathlib import Path

import click
import requests

from . import common
from .common import _c, _err, _ok, _r_json, _req, _json_out, _none_if_empty


def _http_error(r, what: str):
    try:
        d = r.json()
        msg = d.get("detail") or d.get("error") or d
    except Exception:
        msg = r.text[:200]
    _err(f"{what} → HTTP {r.status_code}: {msg}")


def _human(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def _confirm(yes: bool, prompt: str):
    if not yes:
        click.confirm(prompt, abort=True)


def _fetch_to(path: str, params: dict, what: str, name: str, out):
    """GET 下载到文件: out 为 None 存到当前目录 name, 是目录则存到目录/name, '-' 写到标准输出。"""
    r = _req("GET", path, params=params, timeout=600, stream=True)
    if r.status_code >= 400:
        _http_error(r, f"下载 {what}")
    if out == "-":
        for chunk in r.iter_content(65536):
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        return
    name = Path(name).name or "download"
    dest = Path(out) if out else Path(name)
    if dest.is_dir():
        dest = dest / name
    # [large-file 2026-10-21] 先写 .part, 收全 (且与 Content-Length 一致) 再改名: 断网不会留下"看着完整"的半截文件
    size = 0
    part = dest.with_name(dest.name + ".part")
    try:
        with part.open("wb") as fh:
            for chunk in r.iter_content(1024 * 1024):
                fh.write(chunk)
                size += len(chunk)
    except (requests.RequestException, OSError) as e:
        part.unlink(missing_ok=True)
        _err(f"下载 {what} 中断 (已收 {_human(size)}): {e}")
    hdrs = getattr(r, "headers", None) or {}
    expect = str(hdrs.get("Content-Length", ""))
    if expect.isdigit() and not hdrs.get("Content-Encoding") and int(expect) != size:
        part.unlink(missing_ok=True)
        _err(f"下载 {what} 不完整: 收到 {size} / {expect} 字节")
    part.replace(dest)
    _ok(f"已下载 {what} → {dest} ({_human(size)})")


_OUT_HELP = "保存路径或目录 (默认当前目录同名; - 输出到标准输出)"


# ── upload ─────────────────────────────────────────────────
def _upload_one(sid: str, fp: Path) -> dict:
    """multipart 上传 (common._req 只发 JSON, 这里自己用带 cookie 的 session 发)。"""
    url = f"{common.WEB_URL}/api/upload/{sid}"
    try:
        with fp.open("rb") as fh:
            r = common._session().post(url, files={"file": (fp.name, fh)}, timeout=600)
    except requests.RequestException as e:
        _err(f"网络错误: {e}")
    if r.status_code == 401:
        _err("未登录或 cookie 失效, 跑: litecli login")
    if r.status_code >= 400:
        _http_error(r, f"上传 {fp.name}")
    return r.json()


@click.command("upload")
@click.argument("sid")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
def upload(sid: str, files):
    """上传本地文件到会话 SID, 打印服务端路径 (可在对话里引用)."""
    results = [_upload_one(sid, Path(f)) for f in files]
    if _json_out(results if len(results) > 1 else results[0]):
        return
    for d in results:
        _ok(f"{d.get('name')}  {_human(d.get('size'))}  → {d.get('path')}")


# ── artifacts ──────────────────────────────────────────────
@click.group("artifacts")
def artifacts():
    """产出物 (Web 产出物面板 / 会话写入的文件)."""


@artifacts.command("list")
@click.option("--owner", default=None, help="只看某个 owner, 如 timer:daily-sync")
@click.option("--prefix", default=None, help="按 owner 前缀过滤, 如 plugin:")
@click.option("--limit", type=int, default=200, show_default=True, help="最多条数")
def artifacts_list(owner, prefix, limit: int):
    """列出产出物 (最新在前)."""
    params = {"limit": limit}
    if owner:
        params["owner"] = owner
    if prefix:
        params["prefix"] = prefix
    d = _r_json("GET", "/api/artifacts_v2", params=params)
    if _json_out(d):
        return
    for a in _none_if_empty(d.get("items", [])):
        click.echo(f"  {_c(a.get('aid', '?'), 'cyan')}  {a.get('ts_iso', '')}  {a.get('kind', '?'):<5} "
                   f"{_human(a.get('size')):>8}  {a.get('name', '')}  {_c(a.get('owner', ''), 'gray')}")


@artifacts.command("owners")
def artifacts_owners():
    """列出所有产出物 owner."""
    d = _r_json("GET", "/api/artifacts_v2/owners")
    if _json_out(d):
        return
    for o in _none_if_empty(d.get("owners", [])):
        click.echo(f"  {o}")


@artifacts.command("show")
@click.argument("aid")
def artifacts_show(aid: str):
    """显示产出物 AID 的元数据."""
    d = _r_json("GET", f"/api/artifacts_v2/meta/{aid}")
    if _json_out(d):
        return
    for k in ("aid", "name", "kind", "owner", "ts_iso"):
        click.echo(f"  {_c(k, 'cyan')}: {d.get(k, '')}")
    click.echo(f"  {_c('size', 'cyan')}: {_human(d.get('size'))} ({d.get('size')} 字节)")
    if d.get("tags"):
        click.echo(f"  {_c('tags', 'cyan')}: {d['tags']}")


@artifacts.command("get")
@click.argument("aid")
@click.option("-o", "--output", "out", default=None, help=_OUT_HELP)
def artifacts_get(aid: str, out):
    """下载产出物 AID."""
    meta = _r_json("GET", f"/api/artifacts_v2/meta/{aid}")
    _fetch_to(f"/api/artifacts_v2/blob/{aid}", {"download": 1}, aid, meta.get("name") or aid, out)


@artifacts.command("rm")
@click.argument("aid")
@click.option("-y", "--yes", is_flag=True, help="不询问直接删除")
def artifacts_rm(aid: str, yes: bool):
    """删除产出物 AID (从磁盘移除, 不可恢复)."""
    _confirm(yes, f"删除产出物 {aid}? 不可恢复")
    _r_json("DELETE", f"/api/artifacts_v2/{aid}")
    _ok(f"已删除 {aid}")


@artifacts.command("purge")
@click.argument("owner")
@click.option("-y", "--yes", is_flag=True, help="不询问直接清空")
def artifacts_purge(owner: str, yes: bool):
    """删除某个 owner 下的全部产出物 (不可恢复)."""
    _confirm(yes, f"删除 owner {owner} 下全部产出物? 不可恢复")
    d = _r_json("DELETE", f"/api/artifacts_v2/owner/{owner}")
    _ok(f"已清空 {owner}: {d.get('purged_files', 0)} 项")


@artifacts.command("session")
@click.argument("sid")
def artifacts_session(sid: str):
    """列出会话 SID 写入/修改过的文件 (用 litecli media <路径> 下载)."""
    d = _r_json("GET", f"/api/artifacts/{sid}")
    if _json_out(d):
        return
    if d.get("error"):
        _err(f"获取产出物失败: {d['error']}")
    for a in _none_if_empty(d.get("artifacts", [])):
        ts = time.strftime("%m-%d %H:%M", time.localtime(a.get("ts", 0)))
        click.echo(f"  {ts}  {a.get('op', '?'):<9} {_human(a.get('size')):>8}  {a.get('filepath', '?')}")


# ── media ──────────────────────────────────────────────────
@click.command("media")
@click.argument("path")
@click.option("-o", "--output", "out", default=None, help=_OUT_HELP)
def media(path: str, out):
    """按服务端绝对路径下载文件 (上传件/附件/会话产出, 受服务端白名单限制)."""
    _fetch_to("/api/media", {"path": path}, path, path, out)


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [upload, artifacts, media]
