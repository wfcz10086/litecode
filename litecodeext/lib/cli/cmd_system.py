"""bgprocs / questions 子命令组: 后台进程监控与用户提问历史 (对应 routers/health_router.py)。"""
import time

import click

from .common import _c, _err, _ok, _r_json, _json_out, _none_if_empty


# ── bgprocs ────────────────────────────────────────────────
@click.group()
def bgprocs():
    """后台进程: 由 agent 启动的长驻命令."""


@bgprocs.command('list')
def bgprocs_list():
    """列出后台进程 (● 存活 / ○ 已退出)."""
    d = _r_json('GET', '/api/bgprocs')
    if _json_out(d):
        return
    for p in _none_if_empty(d.get('procs', [])):
        dot = _c('●', 'green') if p.get('alive') else _c('○', 'red')
        ts = time.strftime('%m-%d %H:%M:%S', time.localtime(p['ts'])) if p.get('ts') else '?'
        keep = _c('KEEP', 'yellow') if p.get('keep') else _c('temp', 'gray')
        click.echo(f"  {dot} {_c(str(p.get('pid', '?')), 'cyan')}  {keep}  {ts}  "
                   f"sid={p.get('sid') or '-'}  {str(p.get('cmd', ''))[:80]}")


@bgprocs.command('kill')
@click.argument('pid')
@click.option('-y', '--yes', is_flag=True, help='跳过确认')
def bgprocs_kill(pid: str, yes: bool):
    """结束一个后台进程 (先 SIGTERM, 不退再 SIGKILL)."""
    if not pid.isdigit():
        _err(f'PID 必须是数字: {pid}')
    if not yes:
        click.confirm(f'确认结束后台进程 {pid}? 正在运行的输出会中断', abort=True)
    d = _r_json('DELETE', f'/api/bgprocs/{pid}')
    if not d.get('ok'):
        _err(d.get('error') or f'结束失败: {d}')
    _ok(f'已结束 {pid}' + (' (进程已不存在)' if d.get('note') == 'already gone' else ''))


# ── questions ──────────────────────────────────────────────
@click.group()
def questions():
    """用户提问历史 (各端发给 agent 的提问, 最新在前)."""


@questions.command('list')
@click.option('-n', '--limit', default=20, help='最多显示条数')
@click.option('--sid', default='', help='只看某个会话')
def questions_list(limit: int, sid: str):
    """列出最近的用户提问."""
    d = _r_json('GET', '/api/questions')
    qs = d.get('questions', [])
    if sid:
        qs = [q for q in qs if q.get('session') == sid]
    qs = qs[:limit]
    if _json_out({**d, 'questions': qs}):
        return
    for q in _none_if_empty(qs):
        text = ' '.join(str(q.get('q', '')).split())
        click.echo(f"  {q.get('time', '?')}  {_c(q.get('session', '?'), 'cyan')}  {text[:100]}")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [bgprocs, questions]
