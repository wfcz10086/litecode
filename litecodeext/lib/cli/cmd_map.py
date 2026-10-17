"""map 子命令组: 项目地图 (project map watcher) 状态查看与强制刷新。"""
import json

import click

from .common import _c, _err, _ok, _r_json, _json_out, _none_if_empty


@click.group("map")
def project_map():
    """项目地图 (代码结构索引) 状态."""


@project_map.command("status")
def map_status():
    """查看项目地图监听状态."""
    d = _r_json("GET", "/api/map_status")
    if _json_out(d):
        return
    if d.get("error"):
        _err(f"获取失败: {d['error']}")
    if not d.get("enabled"):
        click.echo(f"  状态: {_c('未启用', 'gray')}")
        return
    click.echo(f"  状态: {_c('已启用', 'green')}  监听 {d.get('watched', 0)} 个目录")
    for p in _none_if_empty(d.get("projects", [])):
        click.echo(f"  - {json.dumps(p, ensure_ascii=False)[:200]}")


@project_map.command("update")
@click.option("--root", "project_root", default=None, help="要刷新的项目根目录 (默认服务端 workspace)")
def map_update(project_root):
    """强制重建项目地图."""
    body = {"project_root": project_root} if project_root else {}
    d = _r_json("POST", "/api/map_force_update", json_body=body, timeout=120)
    if d.get("ok"):
        _ok(f"已触发刷新: {d.get('project_root')}")
    else:
        _err(f"刷新失败: {d.get('error', '?')}")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [project_map]
