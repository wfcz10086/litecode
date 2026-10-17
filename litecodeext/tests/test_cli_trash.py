"""litecli trash / tombstones: 回收站与墓碑 (离线, 打桩 lib.cli.common._req)。"""
import json

from _cli_fake import fake, run  # noqa: F401

TRASH = {'count': 1, 'trash': [{'trash_id': 's1-1700000000', 'sid': 's1', 'ts': 1700000000,
                                'expires': 1700600000, 'kept': 4, 'dropped_count': 3, 'reason': 'truncate'}]}


def test_trash_list_human_json_empty_and_sid(fake):
    f = fake({('GET', '/api/trash'): TRASH})
    r = run('trash', 'list')
    assert r.exit_code == 0 and 's1-1700000000' in r.output and '丢弃 3 条' in r.output
    r = run('--json', 'trash', 'list')
    assert json.loads(r.output) == TRASH
    run('trash', 'list', '--sid', 's1')
    assert f.calls[-1][3] == {'sid': 's1'} and f.calls[0][3] is None
    fake({('GET', '/api/trash'): {'count': 0, 'trash': []}})
    assert '（无）' in run('trash', 'list').output


def test_trash_restore_ok_and_missing(fake):
    fake({('POST', '/api/trash/t1/restore'): {'ok': True, 'restored': 3, 'sid': 's1'}})
    r = run('trash', 'restore', 't1')
    assert r.exit_code == 0 and '已恢复 3 条消息 → s1' in r.output
    r = run('trash', 'restore', 'nope')
    assert r.exit_code == 1 and 'HTTP 404' in r.output


def test_trash_rm_confirm_yes_and_missing(fake):
    f = fake({('DELETE', '/api/trash/t1'): {'ok': True}, ('DELETE', '/api/trash/gone'): {'ok': False}})
    r = run('trash', 'rm', 't1', input='n\n')
    assert r.exit_code == 1 and f.calls == []
    r = run('trash', 'rm', 't1', input='y\n')
    assert r.exit_code == 0 and '已删除 t1' in r.output
    r = run('trash', 'rm', 'gone', '-y')
    assert r.exit_code == 1 and '不存在或已过期' in r.output


def test_trash_purge(fake):
    f = fake({('POST', '/api/trash/purge_expired'): {'ok': True, 'removed': 2}})
    r = run('trash', 'purge', input='n\n')
    assert r.exit_code == 1 and f.calls == []
    r = run('trash', 'purge', '-y')
    assert r.exit_code == 0 and '已清理 2 个' in r.output


def test_tombstones_list(fake):
    fake({('GET', '/api/tombstones'): {'sids': ['s1', 's2']}})
    r = run('tombstones', 'list')
    assert r.exit_code == 0 and 's1' in r.output and 's2' in r.output
    assert json.loads(run('--json', 'tombstones', 'list').output) == {'sids': ['s1', 's2']}
    fake({('GET', '/api/tombstones'): {'sids': []}})
    assert '（无）' in run('tombstones', 'list').output


def test_tombstones_rm_and_clear_confirm(fake):
    f = fake({('DELETE', '/api/tombstones/s1'): {'ok': True}, ('DELETE', '/api/tombstones'): {'ok': True}})
    assert run('tombstones', 'rm', 's1', input='n\n').exit_code == 1
    assert run('tombstones', 'clear', input='n\n').exit_code == 1
    assert f.calls == []
    r = run('tombstones', 'rm', 's1', '-y')
    assert r.exit_code == 0 and '已移除墓碑 s1' in r.output
    r = run('tombstones', 'clear', input='y\n')
    assert r.exit_code == 0 and '已清空' in r.output
    assert f.methods() == [('DELETE', '/api/tombstones/s1'), ('DELETE', '/api/tombstones')]


def test_help_in_chinese():
    for grp, cmds in (('trash', ('list', 'restore', 'rm', 'purge')), ('tombstones', ('list', 'rm', 'clear'))):
        for cmd in cmds:
            r = run(grp, cmd, '--help')
            assert r.exit_code == 0 and any('一' <= ch <= '鿿' for ch in r.output), (grp, cmd)
