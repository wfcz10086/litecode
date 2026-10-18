"""DAG 运行详情「工作树」小节接口 (routers/dag_worktree_router.py) + 前端 web_assets/dag_worktrees.js 静态检查。

真建 git 仓库, 经 dag_state._start_dag_internal 跑一个同行冲突的 saved DAG (复用 test_subagent_worktree 的
假 LLM 夹具), 得到带 job["worktrees"] (一条已合并 + 一条冲突未合并) 的真实运行记录, 再走 HTTP 接口:
列出 / diff / 丢弃两步 / 非本运行的工作树拒绝 / 元数据分支被改拒绝 / 未登录 401。
"""
import asyncio
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib.agent import subagent_worktree as wt  # noqa: E402
from test_subagent_worktree import (  # noqa: E402,F401  夹具 env/dagenv 供 pytest 注入
    env, dagenv, _run_saved, _two_coders, _lc_branches, _git)


@pytest.fixture
def api(dagenv, monkeypatch):
    import dag_state
    import plugins.auth.core as auth
    from routers import dag_worktree_router as dr
    monkeypatch.setattr(dr, '_wui', lambda: dag_state)   # web_ui 只是 re-export dag_state 的 _DAG_JOBS/_dag_job_save
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'good': time.time() + 3600})
    nonce = uuid.uuid4().hex[:8]
    ta, tb = f'wtapi a {nonce}', f'wtapi b {nonce}'
    dagenv['scripts'][ta] = [('write_file', {'filepath': 'a.py', 'content': "x = 'A'\n"})]
    dagenv['scripts'][tb] = [('write_file', {'filepath': 'a.py', 'content': "x = 'B'\n"})]
    job = _run_saved(dagenv, _two_coders(ta, tb, workdir=str(dagenv['repo'])))
    job_id = next(k for k, v in dag_state._DAG_JOBS.items() if v is job)
    app = FastAPI()
    app.include_router(dr.router)
    c = TestClient(app)
    c.cookies.set('lc_auth', 'good')
    yield {'c': c, 'job': job, 'job_id': job_id, 'repo': dagenv['repo'], 'dag_state': dag_state}
    dag_state._DAG_JOBS.pop(job_id, None)


def _base(a):
    return f"/api/dags/jobs/{a['job_id']}/worktrees"


def test_list_shows_branch_status_conflicts(api):
    r = api['c'].get(_base(api))
    assert r.status_code == 200, r.text
    d = r.json()
    rows = {w['step_id']: w for w in d['worktrees']}
    assert set(rows) == {'s1', 's2'} and d['unmerged'] == 1
    bad = next(w for w in rows.values() if w['unmerged'])
    good = next(w for w in rows.values() if not w['unmerged'])
    assert bad['status'] == 'conflict' and bad['status_cn'] == '合并冲突' and bad['conflicts'] == ['a.py']
    assert bad['branch'].startswith('lc-agent/') and bad['can_discard'] is True
    assert good['status'] == 'merged' and good['status_cn'] == '已合并' and good['can_discard'] is False


def test_diff_readonly(api):
    bad = api['job']['worktree_unmerged'][0]
    head = _git(api['repo'], 'rev-parse', 'HEAD')
    r = api['c'].get(f"{_base(api)}/{bad['id']}/diff")
    assert r.status_code == 200, r.text
    text = r.json()['diff']
    assert 'a.py' in text and "x = '" in text and len(text) <= 8300
    assert _git(api['repo'], 'rev-parse', 'HEAD') == head and _lc_branches(api['repo']) == [bad['branch']]


def test_discard_two_step(api):
    bad = api['job']['worktree_unmerged'][0]
    url = f"{_base(api)}/{bad['id']}/discard"
    # 第一步 (无确认 / 确认 id 不符): 拒绝, 分支原样保留
    assert api['c'].post(url, json={}).status_code == 400
    assert api['c'].post(url, json={'confirm': 'other'}).status_code == 400
    assert _lc_branches(api['repo']) == [bad['branch']]
    # 第二步: 带 confirm=<wid> 才真丢弃
    r = api['c'].post(url, json={'confirm': bad['id']})
    assert r.status_code == 200 and r.json()['ok'] is True and r.json()['status'] == 'discarded', r.text
    assert _lc_branches(api['repo']) == []
    job = api['job']
    assert job['worktree_unmerged'] == []
    ev = [e for e in job['exec_log'] if e['event'] == 'worktree']
    assert ev[-1]['status'] == 'discarded' and ev[-1].get('by') == 'web'
    # 已落盘, 列表也反映
    import json
    disk = json.loads((api['dag_state']._DAG_JOBS_DIR / f"{api['job_id']}.json").read_text())
    assert disk['worktree_unmerged'] == []
    d = api['c'].get(_base(api)).json()
    assert d['unmerged'] == 0 and all(not w['can_discard'] for w in d['worktrees'])
    # 重复丢弃: 不报错也不动东西
    again = api['c'].post(url, json={'confirm': bad['id']}).json()
    assert again['ok'] is False and again['status'] == 'discarded'


def test_rejects_worktree_not_in_this_run(api, tmp_path):
    c, base = api['c'], _base(api)
    # 另一个真实存在的工作树 (元数据在盘上) 但不属于本运行 → 404, 不能借本运行去 diff/丢弃
    other = asyncio.run(wt.create_worktree(api['repo'], api['repo'], 'coder', 'other'))
    try:
        assert c.get(f'{base}/{other.wid}/diff').status_code == 404
        assert c.post(f'{base}/{other.wid}/discard', json={'confirm': other.wid}).status_code == 404
        assert other.branch in _lc_branches(api['repo'])
    finally:
        asyncio.run(wt.agent_worktree_tool({'action': 'discard', 'id': other.wid}))
    # 非法 id / 任意分支名 / 不存在的 job
    assert c.get(f'{base}/..%2Fmain/diff').status_code in (400, 404)
    assert c.post(f'{base}/lc-agent%2Fx/discard', json={'confirm': 'x'}).status_code in (400, 404)
    assert c.get('/api/dags/jobs/nope/worktrees').status_code == 404
    # 记录里的 id 对得上, 但元数据分支被改成别的 → 409 拒绝
    bad = api['job']['worktree_unmerged'][0]
    meta = wt.load_meta(bad['id'])
    meta['branch'] = 'main'
    wt._save_meta(meta)
    assert c.post(f"{base}/{bad['id']}/discard", json={'confirm': bad['id']}).status_code == 409
    assert c.get(f"{base}/{bad['id']}/diff").status_code == 409
    assert 'main' in _git(api['repo'], 'branch', '--list', 'main')


def test_requires_auth(api):
    c = TestClient(api['c'].app)   # 无 cookie 的新客户端
    base = _base(api)
    bad = api['job']['worktree_unmerged'][0]
    assert c.get(base).status_code == 401
    assert c.get(f"{base}/{bad['id']}/diff").status_code == 401
    assert c.post(f"{base}/{bad['id']}/discard", json={'confirm': bad['id']}).status_code == 401
    assert _lc_branches(api['repo']) == [bad['branch']]


def test_router_registered():
    import routers
    from routers import dag_worktree_router as dr, static_router
    found = routers.discover(include_plugins=False)
    assert dr.router in found and found.index(dr.router) < found.index(static_router.router)


# ── 前端静态检查 ──
JS = ROOT / 'web_assets' / 'dag_worktrees.js'
BUILD_JS = ROOT / 'web_assets' / 'dag_build_view.js'


@pytest.mark.skipif(shutil.which('node') is None, reason='node 不可用')
@pytest.mark.parametrize('path', [JS, BUILD_JS])
def test_js_syntax(path):
    r = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_js_rules_and_wiring():
    js = JS.read_text(encoding='utf-8')
    code = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    assert not re.search(r'\b(prompt|alert|confirm)\s*\(', code), '禁止弹窗, 用内嵌两步按钮'
    assert '/worktrees' in code and '/diff' in code and '/discard' in code
    assert 'confirm: wid' in code and 'armed' in code             # 两步确认
    assert 'pre.textContent = d.diff' in code                    # diff 只读文本, 不当 HTML
    assert len(js.splitlines()) <= 1000 and 'web_assets2' not in js
    build = BUILD_JS.read_text(encoding='utf-8')
    assert build.count('DAGWorktrees.create(jobId)') == 2 and build.count('wtSec.update(job)') == 2
    assert len(build.splitlines()) <= 1000
    html = (ROOT / 'web_ui.html').read_text(encoding='utf-8')
    assert html.index('/assets/dag_worktrees.js') < html.index('/assets/dag_build_view.js')
