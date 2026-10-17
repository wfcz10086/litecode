"""nodes 子命令组: Windows 节点用户管理面 (对应 routers/windows_nodes_router.py 的 /api/windows/nodes*)。

只做管理端: 登记/撤销/卸载/删记录/取本机命令/检查桌面。设备端接口 (enroll / device/* / *.ps1) 不在 CLI 暴露。
命令参数一律用别名, 内部从列表查 id。
"""
import time
from urllib.parse import urlsplit

import click

from . import common as _common  # WEB_URL 实时读取: --url 覆盖只改 common 一处
from .common import _c, _err, _ok, _info, _r_json, _json_out, _none_if_empty

STATES = {'awaiting_join': '等待执行接入命令', 'expired': '接入码已过期', 'installing': '安装中',
          'online': '在线', 'offline': '离线', 'error': '安装或连接失败', 'revoked': '已撤销',
          'uninstall_pending': '等待电脑接收卸载', 'uninstalling': '正在卸载', 'uninstall_failed': '卸载未完成',
          'uninstalled': '已完全卸载'}
STATE_COLORS = {'online': 'green', 'offline': 'yellow', 'error': 'red', 'uninstall_failed': 'red',
                'revoked': 'gray', 'uninstalled': 'gray', 'expired': 'gray'}
UPDATES = {'current': '已是最新', 'updating': '空闲时自动更新', 'legacy': '旧版代理, 需执行 nodes update-command'}
BASE = '/api/windows/nodes'


def _nodes():
    return _r_json('GET', BASE).get('nodes', [])


def _find(alias: str) -> dict:
    nodes = _nodes()
    node = next((n for n in nodes if n.get('alias') == alias), None)
    if not node:
        known = ', '.join(n.get('alias', '') for n in nodes) or '无'
        _err(f'节点别名不存在: {alias} (已登记: {known})')
    return node


def _confirm(yes: bool, prompt: str):
    if not yes:
        click.confirm(prompt, abort=True)


@click.group()
def nodes():
    """Windows 节点管理 (登记/撤销/卸载/检查桌面)."""


@nodes.command('list')
def nodes_list():
    """列出已登记的 Windows 节点及状态."""
    d = _r_json('GET', BASE)
    if _json_out(d):
        return
    for n in _none_if_empty(d.get('nodes', [])):
        state = n.get('state', '?')
        parts = [_c(n.get('alias', '?'), 'cyan'), _c(STATES.get(state, state), STATE_COLORS.get(state, 'gray'))]
        if n.get('computer'):
            parts.append(f"{n['computer']}/{n.get('user') or '?'}")
        if state == 'online':
            parts.append('桌面就绪' if n.get('desktop_ready') else '桌面待检查')
        if (n.get('capabilities') or {}).get('shell'):
            parts.append(_c('shell 已开启', 'magenta'))
        if n.get('update_state'):
            parts.append(UPDATES.get(n['update_state'], n['update_state']))
        if n.get('last_seen'):
            parts.append('最近连接 ' + time.strftime('%m-%d %H:%M', time.localtime(n['last_seen'])))
        if n.get('error'):
            parts.append(_c(str(n['error'])[:60], 'red'))
        click.echo('  ' + '  '.join(parts))


@nodes.command('add')
@click.argument('alias')
@click.option('--server-url', default=None, help='新电脑能访问的 LiteCode Web 地址 (默认用当前 --url)')
def nodes_add(alias: str, server_url):
    """登记新节点并打印一次性接入命令 (十分钟内有效).

    ALIAS 为 1-50 位英文/数字/下划线/连字符。命令在新电脑的管理员 PowerShell 中执行。
    """
    url = (server_url or _common.WEB_URL).rstrip('/')
    d = _r_json('POST', BASE, json_body={'alias': alias, 'server_url': url})
    if _json_out(d):
        return
    _ok(f"已登记 {d.get('alias', alias)} (id={d.get('id', '?')}), 接入码 {d.get('expires_in', 600) // 60} 分钟内有效")
    if urlsplit(url).hostname in ('localhost', '127.0.0.1', '::1'):
        _info('提示: 接入地址只指向本机, 其它电脑无法连回; 请用 --server-url 填新电脑能访问的地址')
    _info('在新电脑: 右键开始按钮 → 终端(管理员) → 粘贴以下命令:')
    click.echo(d.get('command', ''))


@nodes.command('revoke')
@click.argument('alias')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def nodes_revoke(alias: str, yes: bool):
    """撤销节点接入授权 (不删除电脑上的文件)."""
    node = _find(alias)
    _confirm(yes, f'确认撤销节点 {alias} 的接入授权?')
    d = _r_json('DELETE', f"{BASE}/{node['id']}")
    if not d.get('ok'):
        _err(f'撤销失败: {d}')
    _ok(f'已撤销 {alias}')


@nodes.command('uninstall')
@click.argument('alias')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def nodes_uninstall(alias: str, yes: bool):
    """远程完全卸载节点代理 (电脑上线后执行, 收到清理回执才算完成)."""
    node = _find(alias)
    _confirm(yes, f'确认远程完全卸载节点 {alias}?')
    d = _r_json('POST', f"{BASE}/{node['id']}/uninstall")
    state = d.get('state', '?')
    _ok(f'{alias} → {STATES.get(state, state)}')


@nodes.command('remove')
@click.argument('alias')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def nodes_remove(alias: str, yes: bool):
    """删除已撤销/已卸载/已过期节点的记录, 释放别名."""
    node = _find(alias)
    _confirm(yes, f'确认删除节点 {alias} 的记录? 删除后不能再取本机卸载命令')
    _r_json('POST', f"{BASE}/{node['id']}/remove")
    _ok(f'已删除 {alias} 的记录')


def _print_command(alias: str, kind: str):
    node = _find(alias)
    d = _r_json('GET', f"{BASE}/{node['id']}/{kind}-command")
    if _json_out(d):
        return
    _info('请在安装时的 Windows 用户下, 用管理员 PowerShell 执行:')
    click.echo(d.get('command', ''))


@nodes.command('update-command')
@click.argument('alias')
def nodes_update_command(alias: str):
    """打印本机更新代理命令 (旧版代理不会自更新时使用)."""
    _print_command(alias, 'update')


@nodes.command('uninstall-command')
@click.argument('alias')
def nodes_uninstall_command(alias: str):
    """打印本机卸载命令 (远程卸载不可用时在电脑上执行)."""
    _print_command(alias, 'uninstall')


@nodes.command('shell')
@click.argument('alias')
@click.argument('switch', type=click.Choice(['on', 'off']))
@click.option('-y', '--yes', is_flag=True, help='开启时跳过确认')
def nodes_shell(alias: str, switch: str, yes: bool):
    """开启/关闭节点的 run_powershell 命令行能力 (默认关闭, 仅本命令或 Web 可开)."""
    node = _find(alias)
    enabled = switch == 'on'
    if enabled:
        _confirm(yes, f'确认为节点 {alias} 开启 run_powershell 命令行能力? 该节点将可执行任意 PowerShell')
    d = _r_json('POST', f"{BASE}/{node['id']}/shell", json_body={'enabled': enabled})
    if _json_out(d):
        return
    state = (d.get('capabilities') or {}).get('shell')
    _ok(f"{alias} 命令行能力已{'开启' if state else '关闭'}")


@nodes.command('check')
@click.argument('alias')
def nodes_check(alias: str):
    """检查桌面: 让节点截一张图回传 (最长约 60 秒)."""
    node = _find(alias)
    if not _common._json_mode():
        _info(f'正在检查 {alias} 的桌面, 等待电脑回传截图…')
    d = _r_json('POST', f"{BASE}/{node['id']}/check", timeout=130)
    if _json_out(d):
        return
    if d.get('ok'):
        _ok(f"截图已返回: {d.get('image') or '(无路径)'}")
    else:
        _err(d.get('error') or f"桌面暂不可操作 (state={d.get('state', '?')})")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [nodes]
