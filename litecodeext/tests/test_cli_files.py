"""litecli upload / artifacts / media 离线单测 (打桩 lib.cli.common._session, 不连真服务)。"""
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core'), str(Path(__file__).resolve().parent)]
from lib.cli import common  # noqa: E402
import litecli  # noqa: E402
from test_cli_projects_memory import FakeResp, FakeServer  # noqa: E402


@pytest.fixture
def run(monkeypatch):
    def _run(routes, *args, input=None):
        os.environ.pop('LITECLI_OUTPUT', None)
        srv = FakeServer(routes)
        monkeypatch.setattr(common, '_session', srv.session)
        try:
            res = CliRunner().invoke(litecli.cli, list(args), input=input)
        finally:
            os.environ.pop('LITECLI_OUTPUT', None)
        return res, srv
    return _run


def _upload_ok(call):
    name, fh = call['files']['file']
    data = fh.read()
    return FakeResp(200, {'ok': True, 'path': f'/tmp/litecode_workspace/uploads/{name}', 'name': name, 'size': len(data)})


def test_upload_single_multi_json(run, tmp_path):
    a = tmp_path / 'a.txt'
    a.write_text('hello')
    b = tmp_path / 'b.txt'
    b.write_text('hi')
    routes = {('POST', '/api/upload/s1'): _upload_ok}
    res, srv = run(routes, 'upload', 's1', str(a))
    assert res.exit_code == 0, res.output
    assert 'a.txt  5B  → /tmp/litecode_workspace/uploads/a.txt' in res.output
    res, _ = run(routes, '--json', 'upload', 's1', str(a))
    assert json.loads(res.output)['path'].endswith('/a.txt')
    res, srv = run(routes, '--json', 'upload', 's1', str(a), str(b))
    assert [d['name'] for d in json.loads(res.output)] == ['a.txt', 'b.txt'] and len(srv.calls) == 2


def test_upload_errors(run, tmp_path):
    a = tmp_path / 'a.txt'
    a.write_text('x')
    res, _ = run({('POST', '/api/upload/s1'): (413, {'detail': '文件过大'})}, 'upload', 's1', str(a))
    assert res.exit_code == 1 and '413' in res.output and '文件过大' in res.output
    res, _ = run({('POST', '/api/upload/s1'): (401, {'detail': 'x'})}, 'upload', 's1', str(a))
    assert res.exit_code == 1 and 'litecli login' in res.output
    res, _ = run({}, 'upload', 's1', str(tmp_path / 'missing.txt'))
    assert res.exit_code == 2
    res, _ = run({}, 'upload', 's1')
    assert res.exit_code == 2


ARTS = {'sid': 's1', 'count': 2, 'artifacts': [
    {'filepath': '/tmp/litecode_workspace/out/new.py', 'op': 'write', 'size': 2048, 'ts': 0},
    {'filepath': '/tmp/litecode_workspace/out/old.md', 'op': 'overwrite', 'size': 10, 'ts': 0}]}


def test_artifacts_session(run):
    res, _ = run({('GET', '/api/artifacts/s1'): ARTS}, 'artifacts', 'session', 's1')
    assert res.exit_code == 0 and '/tmp/litecode_workspace/out/new.py' in res.output and '2.0KB' in res.output
    res, _ = run({('GET', '/api/artifacts/s1'): ARTS}, '--json', 'artifacts', 'session', 's1')
    assert json.loads(res.output) == ARTS
    res, _ = run({('GET', '/api/artifacts/s1'): {'sid': 's1', 'artifacts': [], 'count': 0}},
                 'artifacts', 'session', 's1')
    assert '（无）' in res.output
    res, _ = run({('GET', '/api/artifacts/s1'): {'error': 'server unavailable'}}, 'artifacts', 'session', 's1')
    assert res.exit_code == 1


ITEM = {'aid': 'abc123', 'owner': 'timer:daily', 'kind': 'pptx', 'name': 'report.pptx', 'size': 3072,
        'ts': 0, 'tags': {'pages': 3}, 'ts_iso': '2026-10-16 10:00:00'}
V2 = {'count': 1, 'items': [ITEM]}


def test_artifacts_v2_list_owners_show(run):
    res, srv = run({('GET', '/api/artifacts_v2'): V2}, 'artifacts', 'list')
    assert res.exit_code == 0 and 'abc123' in res.output and 'report.pptx' in res.output and '3.0KB' in res.output
    assert srv.calls[0]['params'] == {'limit': 200}
    res, srv = run({('GET', '/api/artifacts_v2'): V2}, '--json', 'artifacts', 'list', '--owner', 'timer:daily',
                   '--prefix', 'timer:', '--limit', '5')
    assert json.loads(res.output) == V2
    assert srv.calls[0]['params'] == {'limit': 5, 'owner': 'timer:daily', 'prefix': 'timer:'}
    res, _ = run({('GET', '/api/artifacts_v2'): {'count': 0, 'items': []}}, 'artifacts', 'list')
    assert '（无）' in res.output
    res, _ = run({('GET', '/api/artifacts_v2/owners'): {'owners': ['timer:daily', 'plugin:cad']}}, 'artifacts', 'owners')
    assert 'timer:daily' in res.output and 'plugin:cad' in res.output
    res, _ = run({('GET', '/api/artifacts_v2/owners'): {'owners': []}}, '--json', 'artifacts', 'owners')
    assert json.loads(res.output) == {'owners': []}
    res, _ = run({('GET', '/api/artifacts_v2/meta/abc123'): ITEM}, 'artifacts', 'show', 'abc123')
    assert 'report.pptx' in res.output and '3072 字节' in res.output
    res, _ = run({('GET', '/api/artifacts_v2/meta/abc123'): ITEM}, '--json', 'artifacts', 'show', 'abc123')
    assert json.loads(res.output) == ITEM
    res, _ = run({('GET', '/api/artifacts_v2/meta/zz'): (404, {'detail': 'artifact not found'})},
                 'artifacts', 'show', 'zz')
    assert res.exit_code == 1 and 'artifact not found' in res.output


def test_artifacts_v2_get(run, tmp_path):
    routes = {('GET', '/api/artifacts_v2/meta/abc123'): ITEM,
              ('GET', '/api/artifacts_v2/blob/abc123'): lambda c: FakeResp(200, None, content=b'PPTX')}
    res, srv = run(routes, 'artifacts', 'get', 'abc123', '-o', str(tmp_path))     # 目录 → 用元数据里的名字
    assert res.exit_code == 0, res.output
    assert (tmp_path / 'report.pptx').read_bytes() == b'PPTX'
    assert srv.sent('GET', '/api/artifacts_v2/blob/abc123')[0]['params'] == {'download': 1}
    res, _ = run(routes, 'artifacts', 'get', 'abc123', '-o', '-')
    assert res.output == 'PPTX'
    res, _ = run({('GET', '/api/artifacts_v2/meta/abc123'): ITEM,
                  ('GET', '/api/artifacts_v2/blob/abc123'): (404, {'detail': 'blob missing'})},
                 'artifacts', 'get', 'abc123', '-o', str(tmp_path / 'x'))
    assert res.exit_code == 1 and 'blob missing' in res.output and not (tmp_path / 'x').exists()


def test_artifacts_v2_rm_purge_confirm(run):
    routes = {('DELETE', '/api/artifacts_v2/abc123'): {'ok': True, 'aid': 'abc123'},
              ('DELETE', '/api/artifacts_v2/owner/timer:daily'): {'ok': True, 'purged_files': 4}}
    res, srv = run(routes, 'artifacts', 'rm', 'abc123')
    assert res.exit_code == 1 and not srv.sent('DELETE', '/api/artifacts_v2/abc123')
    res, srv = run(routes, 'artifacts', 'rm', 'abc123', input='y\n')
    assert res.exit_code == 0 and srv.sent('DELETE', '/api/artifacts_v2/abc123')
    res, _ = run({('DELETE', '/api/artifacts_v2/zz'): (404, {'detail': 'artifact not found'})},
                 'artifacts', 'rm', 'zz', '-y')
    assert res.exit_code == 1 and 'not found' in res.output
    res, srv = run(routes, 'artifacts', 'purge', 'timer:daily', input='n\n')
    assert res.exit_code == 1 and not srv.calls
    res, _ = run(routes, 'artifacts', 'purge', 'timer:daily', '-y')
    assert res.exit_code == 0 and '4 项' in res.output


def test_media_download_and_errors(run, tmp_path):
    routes = {('GET', '/api/media'): lambda c: FakeResp(200, None, content=b'bin')}
    out = tmp_path / 'm.bin'
    res, srv = run(routes, 'media', '/tmp/litecode_workspace/uploads/m.bin', '-o', str(out))
    assert res.exit_code == 0 and out.read_bytes() == b'bin'
    assert srv.calls[0]['params'] == {'path': '/tmp/litecode_workspace/uploads/m.bin'}
    res, _ = run({('GET', '/api/media'): (403, {'detail': 'forbidden: path outside allowed dirs'})},
                 'media', '/etc/passwd', '-o', str(tmp_path / 'p'))
    assert res.exit_code == 1 and '403' in res.output and not (tmp_path / 'p').exists()
    res, _ = run({('GET', '/api/media'): (404, {'detail': 'file not found: /nope'})}, 'media', '/nope')
    assert res.exit_code == 1 and 'not found' in res.output


def test_new_commands_have_chinese_help(run):
    for args in (['upload'], ['artifacts'], ['artifacts', 'list'], ['artifacts', 'get'], ['artifacts', 'owners'],
                 ['artifacts', 'show'], ['artifacts', 'rm'], ['artifacts', 'purge'], ['artifacts', 'session'], ['media'],
                 ['map'], ['map', 'status'], ['map', 'update'], ['memory-compress'], ['memory-clear'],
                 ['memory-edit'], ['timer', 'notifications'], ['projects', 'context', 'add'],
                 ['projects', 'decisions', 'clear'], ['projects', 'instructions', 'set'],
                 ['projects', 'sessions', 'rm']):
        res, _ = run({}, *args, '--help')
        assert res.exit_code == 0
        doc = res.output.split('\n\n')[1]
        assert any('一' <= ch <= '鿿' for ch in doc), args
