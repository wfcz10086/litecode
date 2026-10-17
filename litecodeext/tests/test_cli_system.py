"""litecli bgprocs / questions: 后台进程与提问历史 (离线, 打桩 lib.cli.common._req)。"""
import json

from _cli_fake import fake, run  # noqa: F401

PROCS = {'count': 2, 'procs': [
    {'pid': '4321', 'alive': True, 'sid': 's1', 'cmd': 'python3 -m http.server', 'log': '/tmp/a', 'ts': 1700000000, 'keep': True},
    {'pid': '99', 'alive': False, 'sid': '', 'cmd': 'sleep 9', 'log': '', 'ts': 0, 'keep': False},
]}
QS = {'total': 3, 'questions': [
    {'ts': 3, 'time': '2026-10-16 10:00:00', 'session': 's1', 'q': '第三个\n问题'},
    {'ts': 2, 'time': '2026-10-16 09:00:00', 'session': 's2', 'q': '第二个'},
    {'ts': 1, 'time': '2026-10-16 08:00:00', 'session': 's1', 'q': '第一个'},
]}


def test_bgprocs_list(fake):
    fake({('GET', '/api/bgprocs'): PROCS})
    r = run('bgprocs', 'list')
    assert r.exit_code == 0 and '4321' in r.output and 'KEEP' in r.output and 'http.server' in r.output
    assert 'sid=-' in r.output
    assert json.loads(run('--json', 'bgprocs', 'list').output) == PROCS
    fake({('GET', '/api/bgprocs'): {'procs': []}})
    assert '（无）' in run('bgprocs', 'list').output


def test_bgprocs_kill(fake):
    f = fake({('DELETE', '/api/bgprocs/4321'): {'ok': True, 'pid': '4321'},
              ('DELETE', '/api/bgprocs/5'): {'ok': True, 'pid': '5', 'note': 'already gone'},
              ('DELETE', '/api/bgprocs/6'): {'ok': False, 'error': 'server unavailable'}})
    r = run('bgprocs', 'kill', 'abc', '-y')
    assert r.exit_code == 1 and '必须是数字' in r.output and f.calls == []
    r = run('bgprocs', 'kill', '4321', input='n\n')
    assert r.exit_code == 1 and f.calls == []
    r = run('bgprocs', 'kill', '4321', input='y\n')
    assert r.exit_code == 0 and '已结束 4321' in r.output
    assert '已不存在' in run('bgprocs', 'kill', '5', '-y').output
    r = run('bgprocs', 'kill', '6', '-y')
    assert r.exit_code == 1 and 'server unavailable' in r.output


def test_questions_list(fake):
    fake({('GET', '/api/questions'): QS})
    r = run('questions', 'list')
    assert r.exit_code == 0 and '第三个 问题' in r.output and '第一个' in r.output
    r = run('questions', 'list', '-n', '1')
    assert '第三个' in r.output and '第二个' not in r.output
    r = run('questions', 'list', '--sid', 's1')
    assert '第二个' not in r.output and '第一个' in r.output
    d = json.loads(run('--json', 'questions', 'list', '--sid', 's2').output)
    assert d['total'] == 3 and [q['q'] for q in d['questions']] == ['第二个']
    fake({('GET', '/api/questions'): {'questions': []}})
    assert '（无）' in run('questions', 'list').output


def test_help_in_chinese():
    for grp, cmd in (('bgprocs', 'list'), ('bgprocs', 'kill'), ('questions', 'list')):
        r = run(grp, cmd, '--help')
        assert r.exit_code == 0 and any('一' <= ch <= '鿿' for ch in r.output), (grp, cmd)
