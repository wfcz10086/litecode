"""docker 子命令组: 容器 ps/logs/stats/inspect/启停 (从 litecli.py 拆出)."""
import click

from .common import _c, _err, _ok, _r_json, _emit
from .common import _json_out, _none_if_empty


# ── docker (#44) ───────────────────────────────────────────
@click.group()
def docker():
    """Docker 容器管理 (走 web_ui /api/docker/*)."""


@docker.command("ps")
@click.option("-a", "--all", "all_", is_flag=True, help="含已停止")
def docker_ps(all_: bool):
    d = _r_json("GET", f"/api/docker/ps?all={1 if all_ else 0}")
    if _json_out(d):
        return
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    for c in _none_if_empty(d.get("containers", [])):
        scope = _c("*", "green") if c.get("in_scope") else _c("-", "gray")
        state_c = "green" if c.get("state") == "running" else "yellow"
        click.echo(f"  {scope} {_c(c.get('id',''), 'gray')}  {_c(c.get('name',''), 'cyan')}  {_c(c.get('state',''), state_c)}  {c.get('image','')}  {c.get('status','')}")
    click.echo(_c(f"  ({d.get('count',0)} containers, * = in scope)", "gray"))


@docker.command("logs")
@click.argument("name")
@click.option("-n", "--tail", default=100, help="尾部行数 (上限 1000)")
def docker_logs(name: str, tail: int):
    d = _r_json("GET", f"/api/docker/logs?name={name}&tail={tail}")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    click.echo(d.get("text", ""))


@docker.command("stats")
def docker_stats():
    d = _r_json("GET", "/api/docker/stats")
    if _json_out(d):
        return
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    click.echo(f"  {'NAME':16s} {'CPU%':>7s}  {'MEM':>10s}  {'MEM%':>6s}")
    for r in d.get("stats", []):
        click.echo(f"  {r.get('name',''):16s} {r.get('cpu_pct',0):>7.2f}  {r.get('mem_mb',0):>7.1f}MB  {r.get('mem_pct',0):>5.1f}%")


@docker.command("inspect")
@click.argument("name")
def docker_inspect(name: str):
    d = _r_json("GET", f"/api/docker/inspect?name={name}")
    _emit(d)


@docker.command("restart")
@click.argument("name")
def docker_restart(name: str):
    d = _r_json("POST", "/api/docker/restart", json_body={"name": name})
    if d.get("ok"):
        _ok(f"restart {name} ✓")
    else:
        _err(d.get("error", "unknown"))


@docker.command("stop")
@click.argument("name")
def docker_stop(name: str):
    d = _r_json("POST", "/api/docker/stop", json_body={"name": name})
    if d.get("ok"):
        _ok(f"stop {name} ✓")
    else:
        _err(d.get("error", "unknown"))


@docker.command("start")
@click.argument("name")
def docker_start(name: str):
    d = _r_json("POST", "/api/docker/start", json_body={"name": name})
    if d.get("ok"):
        _ok(f"start {name} ✓")
    else:
        _err(d.get("error", "unknown"))


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [docker]
