"""trash / tombstones 子命令组: 回收站 (截断丢弃的消息) 与已删会话墓碑 (对应 routers/sessions_router.py)。"""
import time

import click

from .common import _c, _err, _ok, _r_json, _json_out, _none_if_empty


def _ts(v) -> str:
    return time.strftime('%m-%d %H:%M', time.localtime(v)) if v else '?'


def _confirm(yes: bool, prompt: str):
    if not yes:
        click.confirm(prompt, abort=True)


# ── trash ──────────────────────────────────────────────────
@click.group()
def trash():
    """回收站: 会话截断时丢弃的消息, 过期前可恢复."""


@trash.command('list')
@click.option('--sid', default='', help='只看某个会话的回收条目')
def trash_list(sid: str):
    """列出回收站条目."""
    d = _r_json('GET', '/api/trash', params={'sid': sid} if sid else None)
    if _json_out(d):
        return
    for t in _none_if_empty(d.get('trash', [])):
        click.echo(f"  {_c(t.get('trash_id', '?'), 'cyan')}  sid={t.get('sid', '?')}  "
                   f"丢弃 {t.get('dropped_count', 0)} 条/保留 {t.get('kept', '?')}  "
                   f"{_ts(t.get('ts'))} → 过期 {_ts(t.get('expires'))}  {t.get('reason', '')}")


@trash.command('restore')
@click.argument('trash_id')
def trash_restore(trash_id: str):
    """把回收条目里的消息追加回原会话末尾."""
    d = _r_json('POST', f'/api/trash/{trash_id}/restore')
    _ok(f"已恢复 {d.get('restored', 0)} 条消息 → {d.get('sid', '?')}" + (f" ({d['note']})" if d.get('note') else ''))


@trash.command('rm')
@click.argument('trash_id')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def trash_rm(trash_id: str, yes: bool):
    """永久删除一个回收条目."""
    _confirm(yes, f'确认永久删除回收条目 {trash_id}?')
    d = _r_json('DELETE', f'/api/trash/{trash_id}')
    if not d.get('ok'):
        _err(f'回收条目不存在或已过期: {trash_id}')
    _ok(f'已删除 {trash_id}')


@trash.command('purge')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def trash_purge(yes: bool):
    """清理所有已过期的回收条目."""
    _confirm(yes, '确认清理所有已过期的回收条目?')
    d = _r_json('POST', '/api/trash/purge_expired')
    _ok(f"已清理 {d.get('removed', 0)} 个过期条目")


# ── tombstones ─────────────────────────────────────────────
@click.group()
def tombstones():
    """会话墓碑: 已删除会话的 id, 阻止同步时被重新写回."""


@tombstones.command('list')
def tombstones_list():
    """列出墓碑里的会话 id."""
    d = _r_json('GET', '/api/tombstones')
    if _json_out(d):
        return
    for sid in _none_if_empty(d.get('sids', [])):
        click.echo(f'  {_c(sid, "cyan")}')


@tombstones.command('rm')
@click.argument('sid')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def tombstones_rm(sid: str, yes: bool):
    """移除一个墓碑 (该会话 id 之后可被重新同步/写回)."""
    _confirm(yes, f'确认移除墓碑 {sid}?')
    _r_json('DELETE', f'/api/tombstones/{sid}')
    _ok(f'已移除墓碑 {sid}')


@tombstones.command('clear')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def tombstones_clear(yes: bool):
    """清空全部墓碑."""
    _confirm(yes, '确认清空全部墓碑?')
    _r_json('DELETE', '/api/tombstones')
    _ok('已清空墓碑')


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [trash, tombstones]
