"""登录/登出/whoami 子命令 (从 litecli.py 拆出, 行为不变)."""
import click

from .common import (
    COOKIE_FILE,
    _err,
    _ok,
    _save_cookie,
    _req,
    _r_json,
    _emit,
)
from . import common as _common  # WEB_URL 实时读取: --url 覆盖只改 common 一处


# ── auth ───────────────────────────────────────────────────
@click.command()
@click.option("-p", "--password", prompt=True, hide_input=True, help="登录密码")
def login(password: str):
    """登录并保存 cookie 到 ~/.litecode/cli_cookie.txt."""
    r = _req("POST", "/api/login", json_body={"password": password})
    if r.status_code != 200:
        _err(f"登录失败 HTTP {r.status_code}: {r.text[:200]}")
    if not r.cookies.get("lc_auth"):
        # auth 关闭的情况
        if r.json().get("ok") and "disabled" in r.json().get("msg", ""):
            _ok("auth 未启用, 无需登录")
            return
        _err("登录响应没有 cookie")
    _save_cookie(r.cookies["lc_auth"])
    _ok(f"已登录 {_common.WEB_URL}, cookie 存 {COOKIE_FILE}")


@click.command()
def logout():
    """清除本地 cookie."""
    if COOKIE_FILE.exists():
        COOKIE_FILE.unlink()
    _ok("已清除 cookie")


@click.command()
def whoami():
    """显示登录状态."""
    d = _r_json("GET", "/api/auth/status")
    _emit(d)


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [login, logout, whoami]
