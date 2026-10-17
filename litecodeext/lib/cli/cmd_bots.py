"""wechat / wecom 子命令组: 微信桥与企业微信机器人桥 (从 litecli.py 拆出)."""
from typing import Optional

import click

from .common import _r_json, _emit


# ── wechat ─────────────────────────────────────────────────
@click.group()
def wechat():
    """微信桥."""


@wechat.command("status")
def wechat_status():
    d = _r_json("GET", "/api/wechat/status")
    _emit(d)


@wechat.command("add")
@click.argument("bot_id", required=False, default="")
def wechat_add(bot_id: str):
    """新增 bot 并触发登录 (bot_id 可空, 服务端自动分配, 首次要扫二维码)."""
    body = {"bot_id": bot_id} if bot_id else {}
    d = _r_json("POST", "/api/wechat/bots", json_body=body)
    _emit(d)


@wechat.command("rm")
@click.argument("bot_id")
def wechat_rm(bot_id: str):
    d = _r_json("DELETE", f"/api/wechat/bots/{bot_id}")
    _emit(d)


@wechat.command("relogin")
@click.argument("bot_id")
def wechat_relogin(bot_id: str):
    d = _r_json("POST", f"/api/wechat/bots/{bot_id}/relogin")
    _emit(d)


@wechat.command("send")
@click.argument("bot_id")
@click.argument("to")
@click.argument("text")
def wechat_send(bot_id: str, to: str, text: str):
    d = _r_json("POST", f"/api/wechat/bots/{bot_id}/send", json_body={"to": to, "text": text})
    _emit(d)


@wechat.command("bots")
@click.argument("bot_id", required=False)
def wechat_bots(bot_id: Optional[str]):
    if bot_id:
        d = _r_json("GET", f"/api/wechat/bots/{bot_id}")
    else:
        d = _r_json("GET", "/api/wechat/status")
        d = {"bots": d.get("bots", [])}
    _emit(d)


# ── wecom (企业微信智能机器人) ────────────────────────────
@click.group()
def wecom():
    """企业微信智能机器人桥 (与 /wechat 对称)."""


@wecom.command("status")
def wecom_status():
    d = _r_json("GET", "/api/wecom/status")
    _emit(d)


@wecom.command("add")
@click.argument("bot_id")
@click.argument("secret")
def wecom_add(bot_id: str, secret: str):
    """新增 bot 并上线."""
    d = _r_json("POST", "/api/wecom/bots",
                json_body={"bot_id": bot_id, "secret": secret})
    _emit(d)


@wecom.command("rm")
@click.argument("bot_id")
def wecom_rm(bot_id: str):
    d = _r_json("DELETE", f"/api/wecom/bots/{bot_id}")
    _emit(d)


@wecom.command("relogin")
@click.argument("bot_id")
def wecom_relogin(bot_id: str):
    d = _r_json("POST", f"/api/wecom/bots/{bot_id}/relogin")
    _emit(d)


@wecom.command("send")
@click.argument("bot_id")
@click.argument("userid")
@click.argument("markdown")
def wecom_send(bot_id: str, userid: str, markdown: str):
    """主动推送 markdown 消息."""
    d = _r_json("POST", f"/api/wecom/bots/{bot_id}/send",
                json_body={"userid": userid, "markdown": markdown})
    _emit(d)


@wecom.command("bots")
@click.argument("bot_id", required=False)
def wecom_bots(bot_id: Optional[str]):
    if bot_id:
        d = _r_json("GET", f"/api/wecom/bots/{bot_id}")
    else:
        d = _r_json("GET", "/api/wecom/status")
        d = {"bots": d.get("bots", [])}
    _emit(d)


@wecom.command("messages")
@click.argument("bot_id")
@click.option("--limit", default=20, help="返回条数 (默认 20)")
@click.option("--userid", default="", help="按 userid 过滤")
def wecom_messages(bot_id: str, limit: int, userid: str):
    q = f"?limit={limit}" + (f"&userid={userid}" if userid else "")
    d = _r_json("GET", f"/api/wecom/bots/{bot_id}/messages{q}")
    _emit(d)


@wecom.command("contacts")
@click.argument("bot_id")
def wecom_contacts(bot_id: str):
    d = _r_json("GET", f"/api/wecom/bots/{bot_id}/contacts")
    _emit(d)


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [wechat, wecom]
