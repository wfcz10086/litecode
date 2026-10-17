"""timer 子命令组: 定时任务增删查/手动执行/历史 (从 litecli.py 拆出)."""
import time

import click

from .common import _c, _err, _ok, _info, _r_json, _emit
from .common import _json_out, _none_if_empty


# ── timer ──────────────────────────────────────────────────
@click.group()
def timer():
    """定时任务."""


@timer.command("list")
def timer_list():
    d = _r_json("GET", "/api/timers")
    if _json_out(d):
        return
    if not d.get("available"):
        _err("timer 模块不可用")
    for t in _none_if_empty(d.get("timers", [])):
        en = "✓" if t.get("enabled") else "✗"
        en_color = "green" if t.get("enabled") else "gray"
        click.echo(f"  {_c(en, en_color)}  {_c(t['id'], 'cyan')}  {t['name']}  ({t['type']} {t['schedule']})  run_count={t.get('run_count', 0)}")
        action = t.get("action", {})
        click.echo(f"      → {action.get('type')} {str(action.get('content', ''))[:80]}")


@timer.command("create")
@click.option("--name", required=True)
@click.option("--type", "ttype", type=click.Choice(["cron", "once"]), default="cron")
@click.option("--schedule", required=True, help="cron 表达式 (* * * * *) 或 ISO 时间")
@click.option("--action", "atype", type=click.Choice(["shell", "agent",
              "wechat_msg", "wechat_shell",
              "wecom_msg", "wecom_shell",
              "web_notify", "dag"]), required=True)
@click.option("--target", default="")
@click.option("--content", required=True)
def timer_create(name: str, ttype: str, schedule: str, atype: str, target: str, content: str):
    body = {"name": name, "type": ttype, "schedule": schedule,
            "action_type": atype, "action_target": target, "action_content": content,
            "enabled": True}
    d = _r_json("POST", "/api/timers", json_body=body)
    if d.get("ok"):
        _ok(f"已创建 id={d['timer']['id']}")
    else:
        _err(d.get("error", "创建失败"))


@timer.command("run")
@click.argument("tid")
def timer_run(tid: str):
    """立即手动执行一次."""
    d = _r_json("POST", f"/api/timers/{tid}/run")
    _emit(d)


@timer.command("toggle")
@click.argument("tid")
@click.option("--off/--on", default=False)
def timer_toggle(tid: str, off: bool):
    d = _r_json("PATCH", f"/api/timers/{tid}", json_body={"enabled": not off})
    _ok(f"{'已暂停' if off else '已启用'} {tid}")


@timer.command("rm")
@click.argument("tid")
def timer_rm(tid: str):
    d = _r_json("DELETE", f"/api/timers/{tid}")
    _ok(f"已删除 {tid}")


@timer.command("history")
@click.argument("tid")
def timer_history(tid: str):
    d = _r_json("GET", f"/api/timers/{tid}/history")
    if _json_out(d):
        return
    history = d.get("history", [])
    if not history:
        _info("无历史")
        return
    for h in history[-20:]:
        ts = time.strftime("%m-%d %H:%M:%S", time.localtime(h.get("ts", 0)))
        status_color = "green" if h.get("status") == "ok" else "red"
        click.echo(f"  {ts}  {_c(h.get('status', '?'), status_color)}  {str(h.get('result', ''))[:80]}")


@timer.command("notifications")
def timer_notifications():
    """取走定时器推送的 Web 通知 (服务端取完即清空, Web 端将不再弹出这些)."""
    d = _r_json("GET", "/api/timers/notifications")
    if _json_out(d):
        return
    for n in _none_if_empty(d.get("notifications", [])):
        ts = time.strftime("%m-%d %H:%M:%S", time.localtime(n.get("time", 0)))
        st = n.get("status")
        tag = f"  {_c(st, 'green' if st == 'ok' else 'red')}" if st else ""
        click.echo(f"  {ts}  {_c(n.get('name', '?'), 'cyan')}{tag}  {str(n.get('message', ''))[:120]}")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [timer]
