"""聊天窗口「⏳ 后台进行中」卡片 (web_assets/bg_card.js) 的后端支撑 + 前端纯逻辑。

- /v1/bgprocs 带 sid; 刚结束的任务保留一段时间 (alive=False, exit_code 来自日志 [BG-EXIT code=N]),
  原来进程一死就从登记表删掉, 卡片看不到 "已完成 / 失败", tail 接口也立刻 404
- DELETE /v1/bgprocs/{pid} 按进程组结束: 原来只 kill 外层 bash, 真正干活的子进程变孤儿继续跑
- bg_card.js 的任务说明 / 最后一条进度行提取 (node 跑, 没装 node 时跳过)
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

import tools.bg as BG  # noqa: E402

SID = 'sess-bgcard-test'


@pytest.fixture
def gw(tmp_path, monkeypatch):
    import gateway.sessions as GS
    monkeypatch.setattr(GS, 'require_auth', lambda r: None)
    monkeypatch.setattr(BG, 'WORKSPACE', tmp_path)
    GS._bg_recent.clear()
    app = FastAPI()
    app.include_router(GS.router)
    yield GS, TestClient(app)
    with BG._bg_lock:
        BG._bg_procs.pop(SID, None)
    GS._bg_recent.clear()


def _start(cmd, tmp_path):
    out = asyncio.run(BG._exec_bg(cmd, sid=SID, cwd=tmp_path))
    assert out.startswith('Background started'), out
    pid = out.split('pid=')[1].split()[0]
    return pid, out.split('log_file=')[1].split()[0]


def _wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False


def test_list_has_sid_and_keeps_finished_with_exit_code(gw, tmp_path):
    GS, c = gw
    pid, log = _start('echo "[帧] 01 第 1 次 3s 审阅: ✓"; sleep 4; exit 3', tmp_path)
    run = [p for p in c.get('/v1/bgprocs').json()['procs'] if p['pid'] == pid]
    assert run and run[0]['sid'] == SID and run[0]['alive'] is True and run[0]['exit_code'] is None
    assert _wait(lambda: 'BG-EXIT' in Path(log).read_text())
    _wait(lambda: not _alive(pid), 3)
    for _ in range(2):   # 不只出现一次: 之后的轮询仍能看到结果
        done = [p for p in c.get('/v1/bgprocs').json()['procs'] if p['pid'] == pid]
        assert done and done[0]['alive'] is False and done[0]['exit_code'] == 3
        assert done[0]['sid'] == SID and done[0]['stopped'] is False and done[0]['log'] == log
    GS.RECENT_KEEP_S, keep = 0, GS.RECENT_KEEP_S
    try:
        time.sleep(0.01)
        assert not [p for p in c.get('/v1/bgprocs').json()['procs'] if p['pid'] == pid]
    finally:
        GS.RECENT_KEEP_S = keep


def test_finished_task_tail_still_readable(gw, tmp_path, monkeypatch):
    """web 层 tail 接口按 /v1/bgprocs 的列表找日志 → 结束后的保留期内仍可读"""
    GS, c = gw
    pid, log = _start('echo 进度行A; echo; echo 进度行B', tmp_path)
    assert _wait(lambda: 'BG-EXIT' in Path(log).read_text())
    _wait(lambda: not _alive(pid), 3)
    c.get('/v1/bgprocs')
    from routers import health_router as hr

    async def aproxy(path, method='GET', **kw):
        return c.get(path).json()
    monkeypatch.setattr(hr.S, 'aproxy', aproxy)
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)
    app = FastAPI()
    app.include_router(hr.router)
    d = TestClient(app).get(f'/api/bgprocs/{pid}/tail?lines=20').json()
    assert '进度行B' in d['log'] and '[BG-EXIT code=0]' in d['log']


def test_delete_kills_whole_process_group(gw, tmp_path):
    GS, c = gw
    pid, log = _start('sleep 60 & echo child=$!; wait', tmp_path)
    assert _wait(lambda: 'child=' in Path(log).read_text())
    child = Path(log).read_text().split('child=')[1].split()[0]
    assert _alive(child)
    r = c.delete(f'/v1/bgprocs/{pid}').json()
    assert r['ok'] is True
    assert _wait(lambda: not _alive(child), 5), '子进程没被结束 (只杀了外层 bash)'
    done = [p for p in c.get('/v1/bgprocs').json()['procs'] if p['pid'] == pid]
    assert done and done[0]['alive'] is False and done[0]['stopped'] is True and done[0]['sid'] == SID


def test_delete_unknown_pid(gw):
    _, c = gw
    assert c.delete('/v1/bgprocs/abc').status_code == 400
    assert c.delete('/v1/bgprocs/999999999').status_code in (200, 500)


# ── bg_card.js 纯逻辑 ─────────────────────────────────────────
JS = ROOT / 'web_assets' / 'bg_card.js'


@pytest.mark.skipif(not shutil.which('node'), reason='node 未安装')
def test_bg_card_helpers_node():
    script = f"""
const m = require({json.dumps(str(JS))});
const out = {{
  d1: m.bgDesc("cd /workspace && python3 skills/anime-short/anime_short.py mv --out /workspace/out/捡可乐瓶_写实 --song a.mp3"),
  d2: m.bgDesc("sleep 40; echo 进度行"),
  l1: m.bgLastLine("[帧] 08 第 1 次 10s 审阅: ✓\\n[帧] 09 第 1 次 11s 审阅: ✓\\n\\n   \\n"),
  l2: m.bgLastLine("a\\n[视频] 03 h3 90s 审阅: ✓\\n[BG-EXIT code=0]\\n"),
  l3: m.bgLastLine("下载 10%\\r下载 55%\\r下载 90%\\n"),
  l4: m.bgLastLine(""),
  e1: m.bgExitCode("x\\n[BG-EXIT code=2]\\n"),
  e2: m.bgExitCode("x"),
  t1: m.bgDur(3725), t2: m.bgDur(42),
}};
console.log(JSON.stringify(out));
"""
    r = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o['d1'] == 'anime_short.py mv …/捡可乐瓶_写实'
    assert 'sleep 40' in o['d2']
    assert o['l1'] == '[帧] 09 第 1 次 11s 审阅: ✓'
    assert o['l2'] == '[视频] 03 h3 90s 审阅: ✓'
    assert o['l3'] == '下载 90%'
    assert o['l4'] == ''
    assert o['e1'] == 2 and o['e2'] is None
    assert o['t1'] == '1时2分' and o['t2'] == '42秒'


def test_bg_card_wired_and_no_prompt():
    src = JS.read_text()
    assert 'prompt(' not in src and 'confirmCard(' in src
    assert len(src.splitlines()) <= 1000
    html = (ROOT / 'web_ui.html').read_text()
    assert '/assets/bg_card.js' in html
