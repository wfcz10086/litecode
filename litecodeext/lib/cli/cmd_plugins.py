"""plugins 子命令组: 插件注册表内省与热重扫 (从 litecli.py 拆出)."""
import os

import click

from .common import _c, _err, _ok, _r_json, _emit


# ── plugins ─────────────────────────────────────────────────
@click.group()
def plugins():
    """Plugin registry 内省 / 热重扫 (走 /api/plugins*)."""


@plugins.command("list")
@click.option("-e", "--errors-only", is_flag=True, help="只列加载失败的插件")
def plugins_list(errors_only: bool):
    d = _r_json("GET", "/api/plugins")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    errs = d.get("errors", []) or []
    tools = d.get("tools", []) or []
    if errors_only:
        if not errs:
            _ok("no plugin load errors")
            return
        for e in errs:
            click.echo(f"  {_c('✗', 'red')}  {_c(e.get('path',''), 'gray')}  {e.get('err','')[:200]}")
        return
    if os.environ.get("LITECLI_OUTPUT") == "json":
        _emit(d)
        return
    click.echo(_c(f"  registry: {d.get('count',0)} tools, {len(errs)} errors", "cyan"))
    for t in tools:
        caps = t.get("capabilities", {}) or {}
        badges = []
        if caps.get("safe") is False: badges.append(_c("unsafe", "yellow"))
        if caps.get("long_running"): badges.append(_c("long", "gray"))
        if caps.get("produces_artifact"): badges.append(_c("artifact", "cyan"))
        if caps.get("auth_required"): badges.append(_c("auth", "magenta"))
        badge_s = " ".join(badges)
        name   = (t.get("name", "") or "")[:24].ljust(24)
        origin = (t.get("origin", "") or "?")[:10].ljust(10)
        click.echo(f"  {_c(name, 'green')}  {_c(origin, 'gray')}  {badge_s}")
    if errs:
        click.echo(_c(f"\n  {len(errs)} error(s):", "red"))
        for e in errs:
            click.echo(f"    ✗ {_c(e.get('path',''), 'gray')}  {e.get('err','')[:160]}")


@plugins.command("reload")
def plugins_reload():
    d = _r_json("POST", "/api/plugins/reload")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    before = d.get("count_before", 0)
    after  = d.get("count_after", 0)
    ec     = d.get("error_count", 0)
    delta  = after - before
    arrow  = _c(f"({delta:+d})", "green" if delta >= 0 else "red")
    _ok(f"plugin registry rescanned: {before} → {after} {arrow}, {ec} error(s)")
    for e in (d.get("errors") or [])[:20]:
        click.echo(f"    {_c('✗', 'red')} {_c(e.get('path',''), 'gray')}  {e.get('err','')[:160]}")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [plugins]
