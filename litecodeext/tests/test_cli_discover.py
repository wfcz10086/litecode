"""litecli 子命令插拔: lib/cli/cmd_*.py 声明 COMMANDS 自动注册; LITECODE_CLI_PLUGIN_PATH 外部插件可插可拔。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib import cli as cli_pkg

BUILTIN = {'login', 'logout', 'whoami', 'model', 'timer', 'sessions', 'ws', 'preview', 'dag', 'wechat',
           'wecom', 'docker', 'plugins', 'projects', 'config', 'memory', 'stats', 'health'}


def test_builtin_commands_all_discovered(monkeypatch):
    monkeypatch.delenv(cli_pkg.PLUGIN_ENV, raising=False)
    names = {c.name for c in cli_pkg.discover(taken={'chat', 'repl'})}
    assert BUILTIN <= names and not names & {'chat', 'repl'}


def test_plugin_added_dup_skipped_broken_isolated(tmp_path, monkeypatch, capsys):
    (tmp_path / 'good.py').write_text(
        'import click\n'
        '@click.command()\ndef hello():\n    click.echo("hi")\n'
        '@click.command(name="health")\ndef dup():\n    pass\n'
        'COMMANDS = [hello, dup]\n')
    (tmp_path / 'broken.py').write_text('raise RuntimeError("坏插件")\n')
    monkeypatch.setenv(cli_pkg.PLUGIN_ENV, str(tmp_path))
    cmds = cli_pkg.discover(taken={'chat', 'repl'})
    names = [c.name for c in cmds]
    assert 'hello' in names
    assert names.count('health') == 1 and next(c for c in cmds if c.name == 'health').callback.__module__ \
        == 'lib.cli.cmd_admin'                                  # 内置优先
    err = capsys.readouterr().err
    assert '坏插件' in err and '重名' in err


def test_builtin_name_clash_fails_loudly(monkeypatch):
    monkeypatch.delenv(cli_pkg.PLUGIN_ENV, raising=False)
    with pytest.raises(RuntimeError, match='内置命令重名'):
        cli_pkg.discover(taken={'health'})


def test_entry_registers_discovered_commands():
    import litecli
    assert BUILTIN | {'chat', 'repl'} <= set(litecli.cli.commands)
