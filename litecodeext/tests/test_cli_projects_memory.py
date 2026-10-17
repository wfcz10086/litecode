"""litecli 新增命令离线单测: 项目上下文/决策/指令/项目会话、记忆压缩/清空/编辑、model add、
timer notifications、map status/update。

打桩点只有 lib.cli.common._session (所有 _req/_r_json 都经它发请求), 不连真服务。
"""
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.cli import common  # noqa: E402
import litecli  # noqa: E402


class FakeResp:
    def __init__(self, status=200, body=None, content=b''):
        self.status_code = status
        self._body = body
        self.content = content
        self.text = json.dumps(body, ensure_ascii=False) if body is not None else content.decode(errors='replace')

    def json(self):
        if self._body is None:
            raise ValueError('not json')
        return self._body

    def iter_content(self, n):
        yield self.content


class FakeServer:
    """routes: {(METHOD, path): body | (status, body) | callable(call)->FakeResp}; calls 记录每次请求。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def session(self):
        srv = self

        class _S:
            cookies = type('C', (), {'set': lambda *a, **k: None})()

            def request(self, method, url, json=None, params=None, timeout=None, stream=False, files=None):
                path = url[len(common.WEB_URL):]
                call = {'method': method, 'path': path, 'json': json, 'params': params, 'files': files}
                srv.calls.append(call)
                r = srv.routes.get((method, path))
                if r is None:
                    return FakeResp(404, {'detail': 'Not Found'})
                if callable(r):
                    return r(call)
                if isinstance(r, tuple):
                    return FakeResp(*r)
                return FakeResp(200, r)

            def post(self, url, **kw):
                return self.request('POST', url, **kw)

        return _S()

    def sent(self, method, path):
        return [c for c in self.calls if c['method'] == method and c['path'] == path]


@pytest.fixture
def run(monkeypatch):
    """返回 run(routes, *args, input=None) -> (result, server)。每次清掉 --json 留下的环境变量。"""
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


# ── memory 旧用法不变 ───────────────────────────────────────
MEM = {'context_window': 256000, 'token_estimate': 1200, 'pct': 0.5,
       'l1': {'lines': 12, 'tokens': 300, 'content': '# 记忆\n- a\n'},
       'l2': [{'topic': 'proj', 'tokens': 40}],
       'context_files': [{'name': 'SOUL.md', 'tokens': 99}]}


def test_memory_old_usage_output_unchanged(run):
    res, srv = run({('GET', '/api/memory/s1'): MEM}, 'memory', 's1')
    assert res.exit_code == 0
    assert res.output == ('  ctx=256000  used=1200 (0.5%)\n'
                          '  L1: 12 行 / 300 tok\n'
                          '  L2 proj: 40 tok\n'
                          '  ctx SOUL.md: 99 tok\n')
    assert [c['method'] for c in srv.calls] == ['GET']


def test_memory_old_usage_json_and_help_unchanged(run):
    res, _ = run({('GET', '/api/memory/s1'): MEM}, '--json', 'memory', 's1')
    assert json.loads(res.output) == MEM
    res, _ = run({}, 'memory', '--help')
    assert res.output == ('Usage: cli memory [OPTIONS] SID\n\n'
                          '  显示 session 的记忆 (L1/L2/context_files).\n\n'
                          'Options:\n  -h, --help  Show this message and exit.\n')
    # 子命令名不会被当成 sid 以外的东西: memory compress 仍按 sid="compress" 查记忆
    res, srv = run({('GET', '/api/memory/compress'): MEM}, 'memory', 'compress')
    assert res.exit_code == 0 and srv.calls[0]['path'] == '/api/memory/compress'


# ── memory-compress / clear / edit ─────────────────────────
def test_memory_compress_ok_and_failures(run):
    res, srv = run({('POST', '/api/memory/s1/compress'): {'ok': True}}, 'memory-compress', 's1')
    assert res.exit_code == 0 and '已压缩 s1' in res.output
    res, _ = run({('POST', '/api/memory/s1/compress'): {'ok': False, 'reason': 'no history'}},
                 'memory-compress', 's1')
    assert res.exit_code == 1 and 'no history' in res.output
    res, _ = run({('POST', '/api/memory/s1/compress'): {'error': 'server busy (timeout)'}},
                 'memory-compress', 's1')
    assert res.exit_code == 1 and 'timeout' in res.output
    res, _ = run({('POST', '/api/memory/s1/compress'): (401, {'detail': 'x'})}, 'memory-compress', 's1')
    assert res.exit_code == 1 and 'litecli login' in res.output


def test_memory_clear_requires_confirm(run):
    routes = {('DELETE', '/api/memory/s1'): {'ok': True}}
    res, srv = run(routes, 'memory-clear', 's1')                 # 无输入 → 视为取消
    assert res.exit_code == 1 and not srv.sent('DELETE', '/api/memory/s1')
    res, srv = run(routes, 'memory-clear', 's1', input='n\n')
    assert res.exit_code == 1 and not srv.sent('DELETE', '/api/memory/s1')
    res, srv = run(routes, 'memory-clear', 's1', input='y\n')
    assert res.exit_code == 0 and len(srv.sent('DELETE', '/api/memory/s1')) == 1
    res, srv = run(routes, 'memory-clear', 's1', '-y')
    assert res.exit_code == 0 and '已清空 s1' in res.output
    res, _ = run({('DELETE', '/api/memory/s1'): {'error': 'HTTP 500', 'status': 500}}, 'memory-clear', 's1', '-y')
    assert res.exit_code == 1


def test_memory_edit_file_and_editor(run, tmp_path, monkeypatch):
    f = tmp_path / 'l1.md'
    f.write_text('# 新记忆\n- x\n', encoding='utf-8')
    routes = {('PATCH', '/api/memory/s1/l1'): {'ok': True}, ('GET', '/api/memory/s1'): MEM}
    res, srv = run(routes, 'memory-edit', 's1', '--file', str(f), '-y')
    assert res.exit_code == 0, res.output
    assert srv.sent('PATCH', '/api/memory/s1/l1')[0]['json'] == {'content': '# 新记忆\n- x\n'}
    res, srv = run(routes, 'memory-edit', 's1', '--file', str(f))   # 未确认 → 不写
    assert res.exit_code == 1 and not srv.sent('PATCH', '/api/memory/s1/l1')
    res, srv = run(routes, 'memory-edit', 's1', '--file', '-', '-y', input='来自 stdin')
    assert srv.sent('PATCH', '/api/memory/s1/l1')[0]['json'] == {'content': '来自 stdin'}
    res, _ = run(routes, 'memory-edit', 's1')
    assert res.exit_code == 1 and '二选一' in res.output

    seen = {}

    def fake_edit(text, extension=None):
        seen['old'] = text
        return text + '- 新增\n'
    monkeypatch.setattr('click.edit', fake_edit)
    res, srv = run(routes, 'memory-edit', 's1', '-e')
    assert res.exit_code == 0 and seen['old'] == MEM['l1']['content']
    assert srv.sent('PATCH', '/api/memory/s1/l1')[0]['json'] == {'content': MEM['l1']['content'] + '- 新增\n'}
    monkeypatch.setattr('click.edit', lambda text, extension=None: None)
    res, srv = run(routes, 'memory-edit', 's1', '-e')
    assert res.exit_code == 0 and '未修改' in res.output and not srv.sent('PATCH', '/api/memory/s1/l1')


# ── projects context ───────────────────────────────────────
CTX = {'project_id': 'p1', 'count': 1, 'files': [{'name': 'NOTES.md', 'size': 5, 'preview': 'hello'}]}


def test_projects_context_list_get(run):
    res, _ = run({('GET', '/api/projects/p1/context'): CTX}, 'projects', 'context', 'list', 'p1')
    assert res.exit_code == 0 and 'NOTES.md' in res.output and '5 字' in res.output
    res, _ = run({('GET', '/api/projects/p1/context'): CTX}, '--json', 'projects', 'context', 'list', 'p1')
    assert json.loads(res.output) == CTX
    res, _ = run({('GET', '/api/projects/p1/context'): {'files': [], 'count': 0}},
                 'projects', 'context', 'list', 'p1')
    assert '（无）' in res.output
    res, _ = run({('GET', '/api/projects/nope/context'): (404, {'error': 'project not found'})},
                 'projects', 'context', 'list', 'nope')
    assert res.exit_code == 1 and 'project not found' in res.output
    res, _ = run({('GET', '/api/projects/p1/context/NOTES.md'): {'name': 'NOTES.md', 'content': '全文'}},
                 'projects', 'context', 'get', 'p1', 'NOTES.md')
    assert res.output == '全文\n'
    res, _ = run({('GET', '/api/projects/p1/context/NOTES.md'): {'name': 'NOTES.md', 'content': '全文'}},
                 '--json', 'projects', 'context', 'get', 'p1', 'NOTES.md')
    assert json.loads(res.output)['content'] == '全文'


def test_projects_context_add_rm(run):
    routes = {('GET', '/api/projects/p1/context'): CTX,
              ('PUT', '/api/projects/p1/context/NEW.md'): {'ok': True, 'name': 'NEW.md', 'size': 3},
              ('PUT', '/api/projects/p1/context/NOTES.md'): {'ok': True, 'name': 'NOTES.md', 'size': 3},
              ('PUT', '/api/projects/p1/context/bad.txt'): (400, {'error': 'filename must end with .md'}),
              ('DELETE', '/api/projects/p1/context/NOTES.md'): {'ok': True}}
    res, srv = run(routes, 'projects', 'context', 'add', 'p1', 'NEW.md', '--content', 'abc')
    assert res.exit_code == 0 and srv.sent('PUT', '/api/projects/p1/context/NEW.md')[0]['json'] == {'content': 'abc'}
    res, srv = run(routes, 'projects', 'context', 'add', 'p1', 'NOTES.md', '--content', 'abc')  # 已存在需确认
    assert res.exit_code == 1 and not srv.sent('PUT', '/api/projects/p1/context/NOTES.md')
    res, srv = run(routes, 'projects', 'context', 'add', 'p1', 'NOTES.md', '--content', 'abc', '-y')
    assert res.exit_code == 0 and srv.sent('PUT', '/api/projects/p1/context/NOTES.md')
    res, _ = run(routes, 'projects', 'context', 'add', 'p1', 'bad.txt', '--content', 'x')
    assert res.exit_code == 1 and '.md' in res.output
    res, _ = run(routes, 'projects', 'context', 'add', 'p1', 'NEW.md')
    assert res.exit_code == 1 and '--file' in res.output
    res, srv = run(routes, 'projects', 'context', 'rm', 'p1', 'NOTES.md')
    assert res.exit_code == 1 and not srv.sent('DELETE', '/api/projects/p1/context/NOTES.md')
    res, srv = run(routes, 'projects', 'context', 'rm', 'p1', 'NOTES.md', '-y')
    assert res.exit_code == 0 and srv.sent('DELETE', '/api/projects/p1/context/NOTES.md')


# ── projects decisions / instructions ──────────────────────
def test_projects_decisions(run):
    dec = {'project_id': 'p1', 'content': '# PROJECT_DECISIONS\n\n## t — cli\n\n用 A\n', 'size': 30}
    routes = {('GET', '/api/projects/p1/decisions'): dec,
              ('POST', '/api/projects/p1/decisions'): {'ok': True, 'size': 60},
              ('DELETE', '/api/projects/p1/decisions'): {'ok': True}}
    res, _ = run(routes, 'projects', 'decisions', 'list', 'p1')
    assert res.exit_code == 0 and '用 A' in res.output
    res, _ = run(routes, '--json', 'projects', 'decisions', 'list', 'p1')
    assert json.loads(res.output) == dec
    res, _ = run({('GET', '/api/projects/p1/decisions'): {'content': '', 'size': 0}},
                 'projects', 'decisions', 'list', 'p1')
    assert '（无）' in res.output
    res, srv = run(routes, 'projects', 'decisions', 'add', 'p1', '改用 B', '--author', 'me')
    assert res.exit_code == 0
    assert srv.sent('POST', '/api/projects/p1/decisions')[0]['json'] == {'decision': '改用 B', 'author': 'me'}
    res, _ = run({('POST', '/api/projects/p1/decisions'): (400, {'error': 'decision/content required'})},
                 'projects', 'decisions', 'add', 'p1', ' ')
    assert res.exit_code == 1 and 'required' in res.output
    res, srv = run(routes, 'projects', 'decisions', 'clear', 'p1', input='n\n')
    assert res.exit_code == 1 and not srv.sent('DELETE', '/api/projects/p1/decisions')
    res, srv = run(routes, 'projects', 'decisions', 'clear', 'p1', '-y')
    assert res.exit_code == 0 and srv.sent('DELETE', '/api/projects/p1/decisions')


def test_projects_instructions(run, tmp_path):
    state = {'content': ''}
    routes = {('GET', '/api/projects/p1/instructions'): lambda c: FakeResp(200, {'project_id': 'p1', **state}),
              ('PUT', '/api/projects/p1/instructions'): {'ok': True, 'size': 4}}
    res, _ = run(routes, 'projects', 'instructions', 'get', 'p1')
    assert '（无）' in res.output
    res, srv = run(routes, 'projects', 'instructions', 'set', 'p1', '--content', '说中文')  # 原本为空不需确认
    assert res.exit_code == 0 and srv.sent('PUT', '/api/projects/p1/instructions')[0]['json'] == {'content': '说中文'}
    state['content'] = '旧指令'
    res, _ = run(routes, 'projects', 'instructions', 'get', 'p1')
    assert res.output == '旧指令\n'
    res, _ = run(routes, '--json', 'projects', 'instructions', 'get', 'p1')
    assert json.loads(res.output)['content'] == '旧指令'
    f = tmp_path / 'ins.md'
    f.write_text('新指令', encoding='utf-8')
    res, srv = run(routes, 'projects', 'instructions', 'set', 'p1', '--file', str(f))      # 覆盖需确认
    assert res.exit_code == 1 and not srv.sent('PUT', '/api/projects/p1/instructions')
    res, srv = run(routes, 'projects', 'instructions', 'set', 'p1', '--file', str(f), input='y\n')
    assert res.exit_code == 0 and srv.sent('PUT', '/api/projects/p1/instructions')[0]['json'] == {'content': '新指令'}
    res, _ = run({('GET', '/api/projects/p1/instructions'): {'content': ''},
                  ('PUT', '/api/projects/p1/instructions'): (400, {'error': '项目指令最多 4000 字'})},
                 'projects', 'instructions', 'set', 'p1', '--content', 'x')
    assert res.exit_code == 1 and '最多' in res.output


def test_projects_sessions(run):
    mp = {'map': {'s1': 'p1', 's2': 'p2'}}
    routes = {('GET', '/api/project-sessions'): mp,
              ('POST', '/api/projects/p1/sessions'): {'ok': True, 'session_count': 2},
              ('DELETE', '/api/project-sessions/s1'): {'ok': True, 'session_id': 's1'},
              ('DELETE', '/api/project-sessions/zz'): {'ok': False, 'session_id': 'zz'}}
    res, _ = run(routes, 'projects', 'sessions', 'list')
    assert 's1  →  p1' in res.output and 's2  →  p2' in res.output
    res, _ = run(routes, '--json', 'projects', 'sessions', 'list', '--project', 'p2')
    assert json.loads(res.output) == {'map': {'s2': 'p2'}}
    res, _ = run({('GET', '/api/project-sessions'): {'map': {}}}, 'projects', 'sessions', 'list')
    assert '（无）' in res.output
    res, srv = run(routes, 'projects', 'sessions', 'add', 'p1', 's9')
    assert res.exit_code == 0 and srv.sent('POST', '/api/projects/p1/sessions')[0]['json'] == {'session_id': 's9'}
    res, srv = run(routes, 'projects', 'sessions', 'rm', 's1')
    assert res.exit_code == 1 and not srv.sent('DELETE', '/api/project-sessions/s1')
    res, _ = run(routes, 'projects', 'sessions', 'rm', 's1', '-y')
    assert res.exit_code == 0 and '已把 s1 移出项目' in res.output
    res, _ = run(routes, 'projects', 'sessions', 'rm', 'zz', '-y')
    assert res.exit_code == 1 and '不属于任何项目' in res.output


# ── model add ──────────────────────────────────────────────
def test_model_add_body_and_key_not_echoed(run, monkeypatch):
    routes = {('GET', '/api/models'): {'models': [{'id': 'old-m'}]},
              ('POST', '/api/models/add'): {'ok': True, 'count': 2}}
    res, srv = run(routes, 'model', 'add', '--id', 'new-m', '--url', 'http://h:8000/v1 ',
                   '--type', 'vllm', '--api-key', 'sk-SECRET', '--vision')
    assert res.exit_code == 0, res.output
    assert 'sk-SECRET' not in res.output and '已添加 new-m (共 2 个模型)' in res.output
    body = srv.sent('POST', '/api/models/add')[0]['json']
    assert body == {'id': 'new-m', 'backend_type': 'vllm', 'backend_url': 'http://h:8000/v1',
                    'api_key': 'sk-SECRET', 'api_format': 'openai-chat', 'context_window': 200000,
                    'max_tokens': 18000, 'supports_vision': True, 'enable_thinking': False,
                    'thinking_budget': 8192, 'label': 'new-m (vllm)'}
    monkeypatch.setenv('LITECODE_MODEL_API_KEY', 'sk-ENV')
    res, srv = run(routes, 'model', 'add', '--id', 'new-m', '--url', 'http://h/v1')
    assert srv.sent('POST', '/api/models/add')[0]['json']['api_key'] == 'sk-ENV' and 'sk-ENV' not in res.output


def test_model_add_overwrite_confirm_and_errors(run):
    routes = {('GET', '/api/models'): {'models': [{'id': 'old-m'}]},
              ('POST', '/api/models/add'): {'ok': True, 'count': 1}}
    res, srv = run(routes, 'model', 'add', '--id', 'old-m', '--url', 'http://h/v1')
    assert res.exit_code == 1 and not srv.sent('POST', '/api/models/add')
    res, srv = run(routes, 'model', 'add', '--id', 'old-m', '--url', 'http://h/v1', '-y')
    assert res.exit_code == 0 and srv.sent('POST', '/api/models/add')
    res, _ = run({('GET', '/api/models'): {'models': []},
                  ('POST', '/api/models/add'): {'error': 'HTTP 400', 'status': 400}},
                 'model', 'add', '--id', 'x', '--url', 'u', '--api-key', 'sk-NOPE')
    assert res.exit_code == 1 and 'HTTP 400' in res.output and 'sk-NOPE' not in res.output
    res, _ = run({}, 'model', 'add', '--url', 'u')
    assert res.exit_code == 2
    res, _ = run({}, 'model', 'add', '--help')
    assert '新增模型' in res.output and 'LITECODE_MODEL_API_KEY' in res.output


# ── timer notifications ────────────────────────────────────
def test_timer_notifications(run):
    nots = {'notifications': [{'id': 'a', 'name': '日报', 'message': '跑完了', 'status': 'ok', 'time': 0},
                              {'id': 'b', 'name': '提醒', 'message': '喝水', 'time': 0}]}
    res, _ = run({('GET', '/api/timers/notifications'): nots}, 'timer', 'notifications')
    assert res.exit_code == 0 and '日报  ok  跑完了' in res.output and '提醒  喝水' in res.output
    res, _ = run({('GET', '/api/timers/notifications'): nots}, '--json', 'timer', 'notifications')
    assert json.loads(res.output) == nots
    res, _ = run({('GET', '/api/timers/notifications'): {'notifications': []}}, 'timer', 'notifications')
    assert '（无）' in res.output
    res, _ = run({('GET', '/api/timers/notifications'): (500, {'detail': 'boom'})}, 'timer', 'notifications')
    assert res.exit_code == 1 and 'boom' in res.output


# ── map ────────────────────────────────────────────────────
def test_map_status_and_update(run):
    st = {'enabled': True, 'watched': 1, 'projects': [{'root': '/w', 'files': 3}]}
    res, _ = run({('GET', '/api/map_status'): st}, 'map', 'status')
    assert '已启用' in res.output and '"root": "/w"' in res.output
    res, _ = run({('GET', '/api/map_status'): st}, '--json', 'map', 'status')
    assert json.loads(res.output) == st
    res, _ = run({('GET', '/api/map_status'): {'enabled': False}}, 'map', 'status')
    assert '未启用' in res.output
    res, _ = run({('GET', '/api/map_status'): {'error': 'server unavailable'}}, 'map', 'status')
    assert res.exit_code == 1
    res, srv = run({('POST', '/api/map_force_update'): {'ok': True, 'project_root': '/w'}}, 'map', 'update')
    assert res.exit_code == 0 and srv.calls[0]['json'] == {}
    res, srv = run({('POST', '/api/map_force_update'): {'ok': False, 'error': 'no watcher'}},
                   'map', 'update', '--root', '/x')
    assert res.exit_code == 1 and srv.calls[0]['json'] == {'project_root': '/x'} and 'no watcher' in res.output


def test_projects_list_reads_project_id_and_type():
    """接口返回 ProjectMeta (project_id / project_type); 旧代码读 id / type 显示成 '?'。"""
    from unittest.mock import patch
    from click.testing import CliRunner
    import litecli
    data = {"projects": [{"project_id": "gen_ab12", "name": "视频生成", "project_type": "video", "status": "active"}]}
    with patch("lib.cli.cmd_admin._r_json", return_value=data):
        out = CliRunner().invoke(litecli.cli, ["projects", "list"]).output
    assert "gen_ab12" in out and "(video)" in out and "?" not in out


def test_chat_no_stream_prints_assembled_text():
    from unittest.mock import patch, MagicMock
    from click.testing import CliRunner
    import litecli
    resp = MagicMock(status_code=200)
    resp.text = ('data: {"choices":[{"delta":{"reasoning":"想"}}]}\n\n'
                 'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                 'data: {"choices":[{"delta":{"content":"，世界"}}]}\n\ndata: [DONE]\n\n')
    with patch.object(litecli, "_req", return_value=resp):
        out = CliRunner().invoke(litecli.cli, ["chat", "-s", "s1", "--no-stream", "hi"]).output
    assert out.strip() == "你好，世界"
