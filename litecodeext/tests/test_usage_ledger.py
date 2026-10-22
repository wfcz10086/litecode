"""token 用量账本 + 看板接口: lib/usage_ledger.py / lib/usage_queries.py / routers/usage_router.py /
scripts/usage_backfill.py, 以及 transport 记账外壳 (vllm_stream 打桩上游, 不连真模型)。

每个用例一个临时库 (env LITECODE_USAGE_DB), 互不串。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib import usage_ledger as UL  # noqa: E402
from lib import usage_queries as Q  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / 'usage.db'
    monkeypatch.setenv('LITECODE_USAGE_DB', str(p))
    UL.STORE.close()
    yield p
    UL.STORE.close()


def rows(sql='SELECT * FROM calls ORDER BY id'):
    return UL.STORE.query(sql)


# ── 解析 / 估算 ──────────────────────────────────────────────
def test_parse_usage_variants():
    u = UL.parse_usage({'prompt_tokens': 1000, 'completion_tokens': 200,
                        'prompt_tokens_details': {'cached_tokens': 800},
                        'completion_tokens_details': {'reasoning_tokens': 150}})
    assert u == {'prompt': 1000, 'completion': 200, 'cached': 800, 'reasoning': 150}
    ds = UL.parse_usage({'prompt_tokens': 50, 'completion_tokens': 5, 'prompt_cache_hit_tokens': 40})
    assert ds['cached'] == 40
    an = UL.parse_usage({'input_tokens': 10, 'output_tokens': 3, 'cache_read_input_tokens': 90})
    assert an['prompt'] == 100 and an['cached'] == 90
    assert UL.parse_usage({}) is None and UL.parse_usage(None) is None and UL.parse_usage('x') is None


def test_record_real_usage_and_scope(db):
    tok = UL.begin_turn('web-xxxx', 't1')
    UL.note_skills(['docx'])
    assert UL.record('m1', {'prompt_tokens': 30000, 'completion_tokens': 100,
                            'prompt_tokens_details': {'cached_tokens': 25000}},
                     duration_ms=1234, tools=['write_file'])
    UL.end_turn(tok, ok=True)
    r = rows()[0]
    assert (r['session_id'], r['turn_id'], r['source'], r['user_id']) == ('web-xxxx', 't1', 'web', 'admin')
    assert (r['prompt_tokens'], r['completion_tokens'], r['cached_tokens'], r['total']) == (30000, 100, 25000, 30100)
    assert r['estimated'] == 0 and r['ok'] == 1 and json.loads(r['tools']) == ['write_file']
    assert json.loads(r['skills']) == ['docx']
    t = rows('SELECT * FROM turns')[0]
    assert t['turn_id'] == 't1' and json.loads(t['skills']) == ['docx'] and t['ok'] == 1
    assert UL.current() == {}          # end_turn 复位作用域


def test_record_estimates_when_no_usage(db):
    msgs = [{'role': 'system', 'content': '你好' * 500}, {'role': 'user', 'content': 'hello world ' * 50}]
    assert UL.record('m', None, messages=msgs, out_text='回答' * 20, reasoning_text='想' * 10)
    r = rows()[0]
    assert r['estimated'] == 1 and r['prompt_tokens'] > 1000 and r['completion_tokens'] >= 50
    assert r['reasoning_tokens'] == UL.est_text('想' * 10)


def test_failed_call_without_output_is_zero(db):
    UL.record('m', None, messages=[{'role': 'user', 'content': 'x' * 900}], ok=False)
    r = rows()[0]
    assert r['ok'] == 0 and r['total'] == 0 and r['estimated'] == 0


def test_source_prefixes_and_overrides(db):
    assert [UL.source_of(s) for s in ('web-1', 'wx-a-b', 'wc-1-2', 'cli-9', 'dag:p:1', None)] == \
        ['web', 'wechat', 'wecom', 'cli', 'dag', 'api']
    tok = UL.begin_turn('wx-bot-u1', 't2')
    UL.record('m', {'prompt_tokens': 1, 'completion_tokens': 1}, source='vision')
    UL.end_turn(tok)
    with UL.scope(source='dag', session_id='dag:p', turn_id='dag-j'):
        UL.record('m', {'prompt_tokens': 1, 'completion_tokens': 1}, source='subagent')
    assert [r['source'] for r in rows()] == ['vision', 'dag']
    assert rows()[1]['session_id'] == 'dag:p'


def test_write_failure_never_raises(db, monkeypatch):
    monkeypatch.setenv('LITECODE_USAGE_DB', '/proc/forbidden/usage.db')
    UL.STORE.close()
    assert UL.record('m', {'prompt_tokens': 1, 'completion_tokens': 1}) is False
    UL.end_turn(UL.begin_turn('web-x', 't'))     # 不抛


def test_retention_prunes_old_rows(db, monkeypatch):
    UL.record('m', {'prompt_tokens': 5, 'completion_tokens': 5}, ts=time.time() - 400 * 86400)
    UL.record('m', {'prompt_tokens': 5, 'completion_tokens': 5})
    assert len(rows()) == 2
    UL.STORE.close()                              # 重新打开时清理过期数据 (默认 365 天)
    assert len(rows()) == 1
    monkeypatch.setenv('LITECODE_USAGE_RETENTION_DAYS', '0')   # 0 = 不清理
    UL.record('m', {'prompt_tokens': 5, 'completion_tokens': 5}, ts=time.time() - 900 * 86400)
    UL.STORE.close()
    assert len(rows()) == 2


def test_end_turn_follows_session_rotation(db):
    tok = UL.begin_turn('web-old', 't9')
    UL.record('m', {'prompt_tokens': 2, 'completion_tokens': 2})
    UL.end_turn(tok, session_id='web-new')
    assert rows()[0]['session_id'] == 'web-new'
    assert rows('SELECT session_id FROM turns')[0]['session_id'] == 'web-new'


# ── transport 记账外壳 ───────────────────────────────────────
def _fake_impl(chunks, exc=None):
    async def impl(messages, tools, _meter=None, **kw):
        if _meter is not None:
            _meter.model = 'routed-model'
        for c in chunks:
            yield c
        if exc:
            raise exc
    return impl


def _drain(gen, stop_after=None):
    async def go():
        n = 0
        try:
            async for _ in gen:
                n += 1
                if stop_after and n >= stop_after:
                    break
        finally:
            await gen.aclose()
    asyncio.run(go())


def test_vllm_stream_records_usage(db, monkeypatch):
    from lib import transport
    chunks = [{'choices': [{'delta': {'reasoning_content': '想一下'}}]},
              {'choices': [{'delta': {'content': '好的'}}]},
              {'choices': [{'delta': {'tool_calls': [{'function': {'name': 'load_skill', 'arguments': '{}'}}]}}]},
              {'choices': [], 'usage': {'prompt_tokens': 26000, 'completion_tokens': 40,
                                        'prompt_tokens_details': {'cached_tokens': 25000}}}]
    monkeypatch.setattr(transport, '_vllm_stream_impl', _fake_impl(chunks))
    tok = UL.begin_turn('web-s1', 'turnA')
    _drain(transport.vllm_stream([{'role': 'user', 'content': 'hi'}], []))
    _drain(transport.subagent_stream([{'role': 'user', 'content': 'hi'}], []))
    UL.end_turn(tok)
    a, b = rows()
    assert a['model'] == 'routed-model' and a['source'] == 'web' and a['turn_id'] == 'turnA'
    assert (a['prompt_tokens'], a['cached_tokens'], a['estimated']) == (26000, 25000, 0)
    assert json.loads(a['tools']) == ['load_skill'] and a['reasoning_tokens'] > 0
    assert b['source'] == 'subagent'


def test_vllm_stream_interrupted_is_estimated(db, monkeypatch):
    from lib import transport
    chunks = [{'choices': [{'delta': {'content': '一二三四五'}}]}] * 5
    monkeypatch.setattr(transport, '_vllm_stream_impl', _fake_impl(chunks))
    _drain(transport.vllm_stream([{'role': 'user', 'content': '你好' * 100}], []), stop_after=2)
    r = rows()[0]
    assert r['estimated'] == 1 and r['ok'] == 1 and r['prompt_tokens'] >= 200 and r['completion_tokens'] >= 10


def test_vllm_stream_error_marks_failed(db, monkeypatch):
    from lib import transport
    monkeypatch.setattr(transport, '_vllm_stream_impl', _fake_impl([], exc=RuntimeError('vLLM returned 400')))
    with pytest.raises(RuntimeError):
        _drain(transport.vllm_stream([{'role': 'user', 'content': 'x'}], []))
    r = rows()[0]
    assert r['ok'] == 0 and r['total'] == 0


def test_skill_helpers_fill_turn_skills(db):
    from lib.agent_state import TurnContext
    ctx = TurnContext(session_id='web-k')
    tok = UL.begin_turn('web-k', 'tk', skills=ctx.turn_skills)
    UL.note_skills(['pptx'])
    UL.note_skills(['docx'], ctx=None)
    UL.end_turn(tok)
    assert ctx.turn_skills == {'pptx', 'docx'}


# ── 聚合查询 ────────────────────────────────────────────────
def _seed():
    now = time.time()
    for i, (sid, src, model, sk, ok) in enumerate([
            ('web-a', 'web', 'GLM', ['docx'], 1), ('web-a', 'web', 'GLM', ['docx', 'pptx'], 1),
            ('web-b', 'web', 'Qwen', ['docx'], 0), ('wx-1', 'wechat', 'GLM', [], 1)]):
        tid = f't{i}'
        UL.record(model, {'prompt_tokens': 1000, 'completion_tokens': 100,
                          'prompt_tokens_details': {'cached_tokens': 900},
                          'completion_tokens_details': {'reasoning_tokens': 30}},
                  source=src, session_id=sid, turn_id=tid, ts=now - i, tools=['web_search'] if i % 2 else [],
                  skills=sk)
        UL.record_turn(tid, session_id=sid, source=src, skills=sk, ok=bool(ok), ts=now - i)
    UL.record('GLM', {'prompt_tokens': 10, 'completion_tokens': 1}, source='compress', session_id='web-a',
              ts=now - 3 * 86400)


def test_summary_aggregates(db):
    _seed()
    s = Q.summary('today')
    t = s['totals']
    assert (t['calls'], t['turns'], t['prompt'], t['cached'], t['reasoning']) == (4, 4, 4000, 3600, 120)
    assert t['avg_per_turn'] == 1100 and t['cache_ratio'] == 0.9
    assert s['bucket'] == 'hour' and len(s['series']) == 24
    assert sum(b['cached'] for b in s['series']) == 3600
    assert {m['name']: m['calls'] for m in s['by_model']} == {'GLM': 3, 'Qwen': 1}
    assert sum(b['turns'] for b in s['series']) == 4
    assert sum(c['calls'] for c in s['heatmap']) == 4 and all(0 <= c['dow'] <= 6 for c in s['heatmap'])
    w = Q.summary('7d')
    assert w['totals']['calls'] == 5 and len(w['series']) == 7
    assert w['prev_totals']['calls'] == 0 and Q.summary('all')['prev_totals'] is None
    assert {x['name'] for x in w['by_source']} == {'web', 'wechat', 'compress'}
    assert len(Q.summary('30d')['series']) == 30


def test_skills_sessions_tools(db):
    _seed()
    sk = {r['skill']: r for r in Q.skills('7d')}
    assert sk['docx']['turns'] == 3 and sk['docx']['sessions'] == 2 and sk['docx']['tokens'] == 3300
    assert abs(sk['docx']['success'] - 2 / 3) < 1e-3
    assert sk['pptx']['turns'] == 1
    assert {s['session_id'] for s in sk['docx']['sessions_list']} == {'web-a', 'web-b'}
    ss = Q.sessions('7d')
    assert ss[0]['session_id'] == 'web-a' and ss[0]['turns'] == 2
    tl = Q.tools('7d')
    assert tl[0]['tool'] == 'web_search' and tl[0]['uses'] == 2
    csv = Q.export_csv('all')
    assert csv.startswith('﻿ts,time,') and csv.count('\n') == 6


# ── 回填 ────────────────────────────────────────────────────
def _session_file(d: Path, sid: str, msgs: list):
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{sid}.json').write_text(json.dumps({'id': sid, 'name': '测试会话', 'messages': msgs}, ensure_ascii=False))


def test_backfill_idempotent_and_dry_run(db, tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    import usage_backfill as BF
    d = tmp_path / 'web_sessions'
    old = time.time() - 86400
    _session_file(d, 'web-bf1', [
        {'role': 'user', 'content': 'hi', 'ts': old},
        {'id': 'm1', 'role': 'assistant', 'content': 'ok', 'ts': str(old + 5),
         'usage': {'prompt_tokens': 500, 'completion_tokens': 50, 'elapsed_seconds': 3.2}},
        {'role': 'assistant', 'content': 'x', 'ts': old + 9, 'meta': {
            'usage': {'prompt_tokens': 70, 'completion_tokens': 7}, 'skills': ['docx']}},
        {'role': 'assistant', 'content': 'no usage', 'ts': old + 10}])
    dry = BF.run([d], dry_run=True)
    assert dry['inserted'] == 2 and rows() == []
    first = BF.run([d])
    again = BF.run([d])
    assert first['inserted'] == 2 and again['inserted'] == 0 and again['existing'] == 2
    rs = rows()
    assert len(rs) == 2 and all(r['backfill'] == 1 and r['source'] == 'web' for r in rs)
    assert rs[0]['duration_ms'] == 3200 and rs[0]['turn_id'] == 'bf-web-bf1-m1'
    assert Q.skills('all')[0]['skill'] == 'docx'


def test_backfill_skips_after_live_ledger(db, tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    import usage_backfill as BF
    d = tmp_path / 'ws'
    t0 = time.time() - 1000
    UL.record('m', {'prompt_tokens': 1, 'completion_tokens': 1}, ts=t0)   # 实时记账从 t0 开始
    _session_file(d, 'web-bf2', [
        {'role': 'assistant', 'content': 'a', 'ts': t0 - 50, 'usage': {'prompt_tokens': 9, 'completion_tokens': 1}},
        {'role': 'assistant', 'content': 'b', 'ts': t0 + 50, 'usage': {'prompt_tokens': 9, 'completion_tokens': 1}}])
    st = BF.run([d])
    assert st['inserted'] == 1 and st['skipped_live'] == 1


# ── HTTP 接口 ────────────────────────────────────────────────
@pytest.fixture
def client(db, tmp_path, monkeypatch):
    import plugins.auth.core as auth
    from routers import usage_router as ur
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'good': time.time() + 3600})
    sd = tmp_path / 'web_sessions'
    _session_file(sd, 'web-a', [])
    monkeypatch.setattr(ur.S, 'sessions_dir', sd)
    app = FastAPI()
    app.include_router(ur.router)
    c = TestClient(app)
    c.cookies.set('lc_auth', 'good')
    return c


def test_api_endpoints(client):
    _seed()
    s = client.get('/api/usage/summary?range=7d').json()
    assert s['totals']['calls'] == 5 and s['range'] == '7d'
    assert client.get('/api/usage/summary?range=bogus').json()['range'] == '7d'
    sk = client.get('/api/usage/skills?range=today').json()['skills']
    docx = next(r for r in sk if r['skill'] == 'docx')
    assert {x['title'] for x in docx['sessions_list']} == {'测试会话', 'web-b'}
    ss = client.get('/api/usage/sessions?range=all&limit=2').json()['sessions']
    assert len(ss) == 2 and ss[0]['title'] == '测试会话'
    assert client.get('/api/usage/tools').json()['tools'][0]['tool'] == 'web_search'
    r = client.get('/api/usage/export.csv')
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/csv')
    assert 'attachment' in r.headers['content-disposition'] and 'litecode_usage_all.csv' in r.headers['content-disposition']
    assert len(r.content.decode('utf-8-sig').strip().splitlines()) == 6


def test_summary_prev_window(db):
    now = time.time()
    UL.record('m', {'prompt_tokens': 100, 'completion_tokens': 10}, ts=now - 60)
    UL.record('m', {'prompt_tokens': 50, 'completion_tokens': 5}, ts=now - 9 * 86400)   # 前 7 天窗内
    UL.record('m', {'prompt_tokens': 7, 'completion_tokens': 7}, ts=now - 20 * 86400)   # 窗外
    s = Q.summary('7d', now=now)
    assert s['totals']['total'] == 110 and s['prev_totals']['total'] == 55


def test_api_report_html(client):
    _seed()
    r = client.get('/api/usage/report.html?range=7d')
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/html')
    assert 'litecode_usage_report_7d.html' in r.headers['content-disposition']
    page = r.text
    assert page.startswith('<!doctype html>') and 'window.__UD_REPORT__' in page
    assert 'UDCharts' in page and '.ud-kpis' in page and '近 7 天' in page
    blob = page.split('window.__UD_REPORT__ = ', 1)[1].split(';</script>', 1)[0]
    data = json.loads(blob.replace('<\\/', '</'))
    assert data['summary']['totals']['calls'] == 5 and data['skills'][0]['skill'] == 'docx'
    assert '</script' not in blob


def test_api_requires_login(client):
    client.cookies.clear()
    assert client.get('/api/usage/summary').status_code == 401


def test_api_empty_ledger(client):
    s = client.get('/api/usage/summary?range=today').json()
    assert s['totals']['calls'] == 0 and s['totals']['avg_per_turn'] == 0 and len(s['series']) == 24
    assert client.get('/api/usage/skills').json()['skills'] == []


# ── 前端静态检查 ──────────────────────────────────────────────
def test_frontend_wired():
    html = (ROOT / 'web_ui.html').read_text(encoding='utf-8')
    assert '/assets/usage_dashboard.js' in html and '/assets/usage_dashboard.css' in html
    assert html.index('/assets/usage_charts.js') < html.index('/assets/usage_dashboard.js')
    sbh = html[html.index('id="sbh"'):html.index('id="sb-tools"')]
    assert 'openUsageDash()' in sbh and '看板' in sbh
    js = (ROOT / 'web_assets' / 'usage_dashboard.js').read_text(encoding='utf-8')
    for api in ('/api/usage/summary', '/api/usage/skills', '/api/usage/sessions', '/api/usage/tools',
                '/api/usage/export.csv', '/api/usage/report.html'):
        assert api in js
    assert 'prompt(' not in js and 'alert(' not in js
