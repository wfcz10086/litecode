"""回收站恢复要两端都回来: web 缓存 + agent 侧 server 会话历史。

2026-10-16 CLI 补齐时发现: truncate 两端都截, 但回收记录只存了 web 那份; restore 只写 web 缓存,
load_session 发现 server 文件更新就整份重导, 恢复的消息消失, 模型侧也从没读到过。
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]


class FakeWebUI:
    """仿 web_ui.load_session: server 文件比 web 缓存新就按 server 整份重导 (真实规则的最小复刻)。"""

    def __init__(self, base: Path):
        self.web_dir, self.srv_dir = base / 'web', base / 'srv'
        self.web_dir.mkdir()
        self.srv_dir.mkdir()
        self.CFG = {'paths': {'sessions_dir': str(self.srv_dir)}}

    def load_session(self, sid):
        web, srv = self.web_dir / f'{sid}.json', self.srv_dir / f'{sid}.json'
        if srv.exists() and (not web.exists() or srv.stat().st_mtime > web.stat().st_mtime + 1.0):
            msgs = [m for m in json.loads(srv.read_text())['msgs'] if m['role'] != 'system']
            return {'id': sid, 'messages': [{'role': m['role'], 'content': m.get('content', '')} for m in msgs]}
        return json.loads(web.read_text())

    def save_session(self, d):
        (self.web_dir / f"{d['id']}.json").write_text(json.dumps(d, ensure_ascii=False))


@pytest.fixture
def env(tmp_path, monkeypatch):
    from lib import trash
    from routers import sessions_router as sr
    monkeypatch.setattr(trash, '_TRASH_DIR', tmp_path / 'trash')
    wui = FakeWebUI(tmp_path)
    monkeypatch.setattr(sr, '_wui', lambda: wui)
    monkeypatch.setattr(sr, 'require_auth', lambda request: None)
    monkeypatch.setattr(sr, 'require_owner', lambda d, request: None)
    monkeypatch.setattr(sr, 'audit_log', lambda *a, **k: None)
    app = FastAPI()
    app.include_router(sr.router)
    sid = 's1'
    turns = [('user', '问1'), ('assistant', '答1'), ('user', '问2'), ('assistant', '答2')]
    srv_msgs = [{'role': 'system', 'content': 'sys'}]
    srv_msgs += [{'role': r, 'content': c} for r, c in turns]
    srv_msgs.insert(4, {'role': 'tool', 'content': '工具结果', 'tool_call_id': 't1'})   # server 侧独有的工具消息
    (wui.srv_dir / f'{sid}.json').write_text(json.dumps({'msgs': srv_msgs}, ensure_ascii=False))
    old = time.time() - 10
    os.utime(wui.srv_dir / f'{sid}.json', (old, old))
    wui.save_session({'id': sid, 'messages': [{'role': r, 'content': c} for r, c in turns]})
    return TestClient(app), wui, sid


def test_truncate_then_restore_brings_back_both_sides(env):
    client, wui, sid = env
    r = client.post(f'/api/sessions/{sid}/truncate', json={'keep': 2})
    assert r.status_code == 200 and r.json()['dropped'] == 2
    trash_id = r.json()['trash']['trash_id']
    srv_after_cut = json.loads((wui.srv_dir / f'{sid}.json').read_text())['msgs']
    assert [m['content'] for m in srv_after_cut] == ['sys', '问1', '答1']

    r = client.post(f'/api/trash/{trash_id}/restore')
    assert r.status_code == 200 and r.json()['restored'] == 2 and r.json()['server_restored'] == 3

    srv = json.loads((wui.srv_dir / f'{sid}.json').read_text())['msgs']
    assert [m['content'] for m in srv] == ['sys', '问1', '答1', '问2', '工具结果', '答2']   # server 原格式原样回来 (含工具消息)
    time.sleep(1.1)   # 越过 load_session 的 1 秒判定窗口: 刷新后仍能看到恢复的消息
    assert [m['content'] for m in wui.load_session(sid)['messages']][-2:] == ['问2', '答2']


def test_legacy_trash_without_server_part_still_restores_web(env):
    client, wui, sid = env
    from lib import trash
    meta = trash.snapshot(sid, 2, [{'role': 'user', 'content': '旧记录'}])     # 旧格式: 无 dropped_server_msgs
    r = client.post(f"/api/trash/{meta['trash_id']}/restore")
    assert r.json()['server_restored'] == 0 and r.json()['restored'] == 1


def _realistic_server_msgs():
    tc = lambda i: [{"id": f"c{i}", "type": "function", "function": {"name": "get_tree", "arguments": "{}"}}]
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "问1"},
        {"role": "assistant", "content": "", "tool_calls": tc(1)},
        {"role": "tool", "tool_call_id": "c1", "content": "树1"},
        {"role": "user", "content": "[SYSTEM-PLAN-GATE] 先拆解"},          # 系统注入, web 不显示
        {"role": "assistant", "content": "答1"},
        {"role": "user", "content": [{"type": "text", "text": "问2"}, {"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "assistant", "content": "", "tool_calls": tc(2)},
        {"role": "tool", "tool_call_id": "c2", "content": "树2"},
        {"role": "assistant", "content": ""},                                # 空 assistant, web 跳过
        {"role": "assistant", "content": "答2"},
    ]


def test_server_cut_matches_web_converter_for_every_keep():
    """性质: 对任意 keep, 用真实 web 转换器转换 server[:cut] 恰好得到 web[:keep] (两端截在同一处)。"""
    import web_ui
    from routers.sessions_router import _server_cut_index
    srv = _realistic_server_msgs()
    view = lambda msgs: [(m["role"], str(m["content"])) for m in web_ui._convert_server_session("s", {"msgs": msgs})["messages"]]
    web = view(srv)
    assert len(web) == 6
    for keep in range(len(web) + 1):
        cut = _server_cut_index(srv, keep, web_ui._is_sys_inject_text)
        assert view(srv[:cut]) == web[:keep], keep
        kept = srv[:cut]
        open_calls = {c["id"] for m in kept for c in (m.get("tool_calls") or [])}
        answered = {m["tool_call_id"] for m in kept if m["role"] == "tool"}
        assert open_calls == answered, f"keep={keep} 留下没有结果的 tool_call"
