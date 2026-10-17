"""litecli nodes: Windows 节点管理面 (离线, 打桩 lib.cli.common._req)。"""
import json

from _cli_fake import fake, run  # noqa: F401
from lib.cli import common

B = '/api/windows/nodes'
NODES = {'nodes': [
    {'id': 'a' * 32, 'alias': 'win-a', 'state': 'online', 'computer': 'PC1', 'user': 'bob',
     'desktop_ready': True, 'update_state': 'legacy', 'last_seen': 1700000000},
    {'id': 'b' * 32, 'alias': 'win-b', 'state': 'revoked'},
]}


def test_list_human_and_json(fake):
    fake({('GET', B): NODES})
    r = run('nodes', 'list')
    assert r.exit_code == 0, r.output
    assert 'win-a' in r.output and '在线' in r.output and 'PC1/bob' in r.output and '桌面就绪' in r.output
    assert 'win-b' in r.output and '已撤销' in r.output
    r = run('--json', 'nodes', 'list')
    assert r.exit_code == 0 and json.loads(r.output) == NODES


def test_list_empty(fake):
    fake({('GET', B): {'nodes': []}})
    r = run('nodes', 'list')
    assert r.exit_code == 0 and '（无）' in r.output


def test_add_prints_command_and_sends_server_url(fake, monkeypatch):
    monkeypatch.setattr(common, 'WEB_URL', 'http://127.0.0.1:18790')
    f = fake({('POST', B): {'alias': 'win-c', 'id': 'c' * 32, 'expires_in': 600,
                            'command': 'powershell.exe -EncodedCommand XYZ', 'state': 'awaiting_join'}})
    r = run('nodes', 'add', 'win-c')
    assert r.exit_code == 0, r.output
    assert 'powershell.exe -EncodedCommand XYZ' in r.output and '10 分钟' in r.output
    assert '只指向本机' in r.output
    assert f.calls[0][2] == {'alias': 'win-c', 'server_url': 'http://127.0.0.1:18790'}
    r = run('nodes', 'add', 'win-c', '--server-url', 'https://lc.example.com/')
    assert f.calls[1][2]['server_url'] == 'https://lc.example.com' and '只指向本机' not in r.output


def test_add_server_error_surfaces(fake):
    fake({('POST', B): (400, {'detail': '节点别名已存在，不覆盖已有机器'})})
    r = run('nodes', 'add', 'win-a')
    assert r.exit_code == 1 and '节点别名已存在' in r.output


def test_unknown_alias_errors_for_every_command(fake):
    for cmd in (['revoke', '-y'], ['uninstall', '-y'], ['remove', '-y'], ['update-command'],
                ['uninstall-command'], ['check']):
        f = fake({('GET', B): NODES})
        r = run('nodes', cmd[0], 'nope', *cmd[1:])
        assert r.exit_code == 1, cmd
        assert '节点别名不存在: nope' in r.output and 'win-a' in r.output
        assert f.methods() == [('GET', B)]          # 没有发出任何写请求


def test_revoke_confirm_and_yes(fake):
    f = fake({('GET', B): NODES, ('DELETE', f"{B}/{'a' * 32}"): {'ok': True}})
    r = run('nodes', 'revoke', 'win-a', input='n\n')
    assert r.exit_code == 1 and ('DELETE', f"{B}/{'a' * 32}") not in f.methods()
    r = run('nodes', 'revoke', 'win-a', input='y\n')
    assert r.exit_code == 0 and '已撤销 win-a' in r.output
    r = run('nodes', 'revoke', 'win-a', '-y')
    assert r.exit_code == 0 and f.methods().count(('DELETE', f"{B}/{'a' * 32}")) == 2


def test_uninstall(fake):
    f = fake({('GET', B): NODES, ('POST', f"{B}/{'a' * 32}/uninstall"): {'state': 'uninstall_pending'}})
    r = run('nodes', 'uninstall', 'win-a', input='n\n')
    assert r.exit_code == 1 and len(f.calls) == 1
    r = run('nodes', 'uninstall', 'win-a', '-y')
    assert r.exit_code == 0 and '等待电脑接收卸载' in r.output


def test_remove_ok_and_refused(fake):
    f = fake({('GET', B): NODES, ('POST', f"{B}/{'b' * 32}/remove"): {'ok': True},
              ('POST', f"{B}/{'a' * 32}/remove"): (400, {'detail': '请先撤销接入或完全卸载，再删除记录'})})
    r = run('nodes', 'remove', 'win-b', input='n\n')
    assert r.exit_code == 1 and len(f.calls) == 1
    r = run('nodes', 'remove', 'win-b', '-y')
    assert r.exit_code == 0 and '已删除 win-b' in r.output
    r = run('nodes', 'remove', 'win-a', '-y')
    assert r.exit_code == 1 and '请先撤销' in r.output


def test_update_and_uninstall_command(fake):
    fake({('GET', B): NODES,
          ('GET', f"{B}/{'a' * 32}/update-command"): {'command': 'UPD'},
          ('GET', f"{B}/{'a' * 32}/uninstall-command"): {'command': 'UNI'}})
    r = run('nodes', 'update-command', 'win-a')
    assert r.exit_code == 0 and r.output.strip().endswith('UPD')
    r = run('nodes', 'uninstall-command', 'win-a')
    assert r.exit_code == 0 and r.output.strip().endswith('UNI')
    r = run('--json', 'nodes', 'uninstall-command', 'win-a')
    assert json.loads(r.output) == {'command': 'UNI'}


def test_check(fake):
    path = f"{B}/{'a' * 32}/check"
    fake({('GET', B): NODES, ('POST', path): {'ok': True, 'image': '/ws/uploads/x.png'}})
    r = run('nodes', 'check', 'win-a')
    assert r.exit_code == 0 and '/ws/uploads/x.png' in r.output
    fake({('GET', B): NODES, ('POST', path): {'ok': False, 'error': '桌面锁定'}})
    r = run('nodes', 'check', 'win-a')
    assert r.exit_code == 1 and '桌面锁定' in r.output
    fake({('GET', B): NODES, ('POST', path): {'ok': True, 'image': 'p'}})
    r = run('--json', 'nodes', 'check', 'win-a')
    assert json.loads(r.output) == {'ok': True, 'image': 'p'}


def test_help_in_chinese():
    for cmd in ('list', 'add', 'revoke', 'uninstall', 'remove', 'update-command', 'uninstall-command', 'check'):
        r = run('nodes', cmd, '--help')
        assert r.exit_code == 0 and any('一' <= ch <= '鿿' for ch in r.output), cmd
