"""原文证据采集只读会话、严格配对和安全新建文件；不连接目标机。"""
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/windows-container-dev/assets/capture-evidence.py'
spec = importlib.util.spec_from_file_location('windows_evidence_capture', SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
SID = 'web-evidence-test'


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / 'sessions').mkdir()
    (tmp_path / 'uploads').mkdir()
    return tmp_path


def pair(key='call-1', tool='windows_desktop', args=None, response=None):
    arguments = json.dumps(args or {'alias': 'test-node', 'action': 'screenshot'},
                           ensure_ascii=False, indent=2)
    call = {'id': key, 'type': 'function', 'function': {'name': tool, 'arguments': arguments}}
    result = response if response is not None else '{"ok":true,"text":"中文原文"}\r\n[image] /test/屏幕.png\n'
    return [{'role': 'assistant', 'content': 'DO_NOT_EXPORT_SUMMARY',
             'reasoning_content': 'DO_NOT_EXPORT_REASONING', 'tool_calls': [call]},
            {'role': 'tool', 'tool_call_id': key, 'content': result}]


def save(workspace, messages, wal=None):
    source = workspace / 'sessions' / (SID + '.json')
    source.write_text(json.dumps({'sid': SID, 'msgs': messages}, ensure_ascii=False), encoding='utf-8')
    if wal is not None:
        source.with_suffix('.wal').write_bytes(wal)
    return source


def collect(workspace, keys=None, output='raw-evidence.json'):
    return capture.capture(workspace, SID, keys or ['call-1'], output)


def test_exact_raw_text_deduplication_readonly_and_cli_metadata(workspace, monkeypatch, capsys):
    first = pair(tool='ssh_exec', args={'alias': 'test-node', 'shell': 'powershell',
                                      'cmd': 'Write-Output "中文\n第二行"'},
                 response='[ssh test@127.0.0.1:2223] rc=0\n--- stdout ---\n中文\r\n第二行\n')
    second = pair('call-2', tool='windows_system', args={'alias': 'test-node', 'action': 'system_info'})
    # 快照与 WAL 重叠；最后一行包括不完整 UTF-8，不能破坏前面的完整证据。
    wal = b''.join(json.dumps(m, ensure_ascii=False).encode('utf-8') + b'\n' for m in first + second)
    source = save(workspace, first, wal + b'{"role":"tool","content":"\xe4\xb8')
    originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in [source, source.with_suffix('.wal')]}
    monkeypatch.setattr(capture, 'WORKSPACE', workspace)

    assert capture.main(['--session', SID, '--tool-call-id', 'call-2', '--tool-call-id', 'call-1',
                         '--tool-call-id', 'call-2', '--output', 'raw-evidence.json']) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed == {'path': str(workspace / 'uploads/raw-evidence.json'), 'count': 2}
    output = Path(printed['path'])
    document = json.loads(output.read_text())
    assert document['records'] == [
        {'tool_call': second[0]['tool_calls'][0], 'raw_response': second[1]['content']},
        {'tool_call': first[0]['tool_calls'][0], 'raw_response': first[1]['content']}]
    assert 'DO_NOT_EXPORT' not in output.read_text()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == before for p, before in originals.items())


def test_wal_only_complete_pair(workspace):
    messages = pair()
    wal = workspace / 'sessions' / (SID + '.wal')
    wal.write_text('\n'.join(json.dumps(m, ensure_ascii=False) for m in messages), encoding='utf-8')
    result = collect(workspace)
    assert json.loads(Path(result['path']).read_text())['records'][0]['raw_response'] == messages[1]['content']


def test_incomplete_wal_tail_cannot_supply_a_missing_result(workspace):
    save(workspace, pair()[:1], b'{"role":"tool","tool_call_id":"call-1","content":"partial')
    with pytest.raises(capture.EvidenceError, match='缺失'):
        collect(workspace)
    assert not list((workspace / 'uploads').iterdir())


def test_explicit_nonempty_call_ids_are_required(workspace):
    save(workspace, pair())
    with pytest.raises(capture.EvidenceError, match='显式'):
        capture.capture(workspace, SID, [], 'raw.json')


@pytest.mark.parametrize('messages', [pair()[:1], pair()[1:], [], pair(tool='execute_shell'),
    pair(response='first line [trimmed]'), pair(response='first line [budget-trimmed]'),
    pair(response='[cleared:ssh_exec 1234c] partial'),
    pair(response='[CLEARED: loop detected, 1234c removed]'),
    pair(response='[SYSTEM OVERRIDE] previous result replaced'),
    pair(response='first\n...[subagent truncated 1234c]...\nlast'),
    pair(response='first\n...(truncated)'), pair(response='{"stdout_truncated": true}'),
    pair(response='{"data":{"truncated":true}}'), pair(response='')])
def test_missing_disallowed_and_partial_results_fail_closed(workspace, messages):
    save(workspace, messages)
    with pytest.raises(capture.EvidenceError):
        collect(workspace)
    assert not list((workspace / 'uploads').iterdir())


@pytest.mark.parametrize(('args', 'response'), [
    ({'action': 'type_secret', 'credential': 'test-ref', 'field': 'username'}, 'ok'),
    ({'password': 'DO_NOT_LEAK'}, 'ok'), ({'jump': {'key_file': '/private/id_ed25519'}}, 'ok'),
    ({'cmd': '$env:API_TOKEN="DO_NOT_LEAK"'}, 'ok'),
    ({'nested': [{'private-key': 'DO_NOT_LEAK'}]}, 'ok'),
    ({'action': 'screenshot'}, 'Set-Cookie: session=DO_NOT_LEAK'),
    ({'action': 'screenshot'}, 'Authorization: Bearer DO_NOT_LEAK'),
    ({'action': 'screenshot'}, '密码：DO_NOT_LEAK'),
    ({'action': 'screenshot'}, 'access_code=DO_NOT_LEAK'),
    ({'action': 'screenshot'}, '-----BEGIN OPENSSH PRIVATE KEY-----\nDO_NOT_LEAK'),
    ({'action': 'screenshot'}, '{"pass\\u0077ord":"DO_NOT_LEAK"}')])
def test_sensitive_calls_or_results_are_rejected_without_echo(workspace, monkeypatch, capsys, args, response):
    save(workspace, pair(args=args, response=response))
    monkeypatch.setattr(capture, 'WORKSPACE', workspace)
    assert capture.main(['--session', SID, '--tool-call-id', 'call-1', '--output', 'raw.json']) == 1
    printed = capsys.readouterr()
    assert printed.out == ''
    assert '拒绝保存' in printed.err and 'DO_NOT_LEAK' not in printed.err
    assert not list((workspace / 'uploads').iterdir())


@pytest.mark.parametrize('where', ['arguments', 'response'])
def test_duplicate_json_keys_cannot_hide_sensitive_originals(workspace, where):
    messages = pair()
    if where == 'arguments':
        messages[0]['tool_calls'][0]['function']['arguments'] = (
            '{"alias":"test-node","text":"password=DO_NOT_LEAK","text":"hello"}')
    else:
        messages[1]['content'] = '{"pass\\u0077ord":"DO_NOT_LEAK","password":"other"}'
    save(workspace, messages)
    with pytest.raises(capture.EvidenceError):
        collect(workspace)
    assert not list((workspace / 'uploads').iterdir())


@pytest.mark.parametrize('sid', ['../outside', r'bad\sid', 'bad.sid', '', '中文'])
def test_unsafe_session_ids_are_rejected(workspace, sid):
    with pytest.raises(capture.EvidenceError):
        capture.capture(workspace, sid, ['call-1'], 'raw.json')


@pytest.mark.parametrize('output', ['../outside.json', '/tmp/outside-capture.json', 'nested/raw.json'])
def test_output_cannot_escape_uploads_or_use_subdirectories(workspace, output):
    save(workspace, pair())
    with pytest.raises(capture.EvidenceError):
        collect(workspace, output=output)
    assert not list((workspace / 'uploads').iterdir())


def test_existing_and_symlink_outputs_are_never_overwritten(workspace):
    save(workspace, pair())
    existing = workspace / 'uploads/existing.json'
    existing.write_bytes(b'KEEP ORIGINAL')
    link = workspace / 'uploads/link.json'
    link.symlink_to(workspace / 'uploads/not-created.json')
    for output in [existing, link]:
        with pytest.raises(capture.EvidenceError):
            collect(workspace, output=output)
    assert existing.read_bytes() == b'KEEP ORIGINAL'
    assert not (workspace / 'uploads/not-created.json').exists()


@pytest.mark.parametrize('source_kind', ['snapshot', 'sessions', 'uploads'])
def test_symlink_sources_and_uploads_are_rejected(workspace, source_kind):
    source = save(workspace, pair())
    original = source if source_kind == 'snapshot' else workspace / source_kind
    moved = original.with_name(original.name + '-original')
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=source_kind != 'snapshot')
    with pytest.raises((capture.EvidenceError, OSError)):
        collect(workspace)
    assert not list((workspace / 'uploads').iterdir())


@pytest.mark.parametrize('change', ['call', 'result'])
def test_duplicate_id_with_conflicting_call_or_result_is_rejected(workspace, change):
    original = pair()
    conflict = pair(args={'alias': 'other-node'} if change == 'call' else None,
                    response='different response' if change == 'result' else None)
    wal = b''.join(json.dumps(m).encode() + b'\n' for m in conflict)
    save(workspace, original, wal)
    with pytest.raises(capture.EvidenceError, match='冲突'):
        collect(workspace)


@pytest.mark.parametrize('wal', [b'{broken}\n{}\n', b'{broken}\n',
    b'{"role":"tool","content":"first","content":"second"}'])
def test_damaged_middle_or_complete_wal_lines_are_rejected(workspace, wal):
    save(workspace, pair(), wal)
    with pytest.raises(capture.EvidenceError):
        collect(workspace)


def test_concurrent_session_change_fails_closed(workspace, monkeypatch):
    save(workspace, pair())
    real_stat = capture.os.stat

    def changed(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if path == SID + '.json' and 'dir_fd' in kwargs:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                                   st_size=info.st_size, st_mtime_ns=info.st_mtime_ns + 1)
        return info

    monkeypatch.setattr(capture.os, 'stat', changed)
    with pytest.raises(capture.EvidenceError, match='写入或压实'):
        collect(workspace)
    assert not list((workspace / 'uploads').iterdir())
