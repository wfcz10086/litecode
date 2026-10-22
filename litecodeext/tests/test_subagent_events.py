"""子代理过程记录 + 分支操作 (lib/agent/subagent_events.py / subagent_branch.py, gateway/subagents.py 新接口)。

覆盖: 真实子代理循环 (_run_subagent_core, 只打桩 LLM 流与 execute_tool) 产生的事件顺序/字段/脱敏;
环形缓冲上限; 落盘 + 模拟重启后读回; ?after= 增量语义; 子代理里再派子代理的 parent_id 嵌套;
新接口的会话隔离 (别的会话 404); 真 git 仓库上的 看改动 / 合并 (冲突回滚不留半合并) / 丢弃 / 基分支校验;
agent.worktree_auto_merge=false 时只提交保留 (等界面审阅)。
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.agent import subagent as sa  # noqa: E402
from lib.agent import subagent_registry as reg  # noqa: E402
from lib.agent import subagent_events as sev  # noqa: E402
from lib.agent import subagent_branch as br  # noqa: E402
from lib.agent import subagent_worktree as wt  # noqa: E402

TOKEN = "gw-test-token"


def _git(repo, *args):
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=str(repo),
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    old_over, old_restored = reg.WORKSPACE_OVERRIDE, reg._RESTORED
    reg._RECORDS.clear()
    reg.WORKSPACE_OVERRIDE = tmp_path
    reg._RESTORED = True
    monkeypatch.setattr(wt, "WORKSPACE_OVERRIDE", tmp_path)
    yield tmp_path
    reg._RECORDS.clear()
    reg.WORKSPACE_OVERRIDE = old_over
    reg._RESTORED = old_restored


def _kinds(rec):
    return [e["kind"] for e in rec.events]


# ═══════════════════ 事件记录 (真实子代理循环) ═══════════════════

def test_real_loop_records_steps(ws, monkeypatch):
    rounds = iter([
        [{"choices": [{"delta": {"reasoning_content": "先看看目录结构"}}]},
         {"choices": [{"delta": {"content": "我先列一下目录"}}]},
         {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {
             "name": "execute_shell", "arguments": json.dumps({"command": "ls", "password": "hunter2"})}}]}}]}],
        [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_2", "function": {
            "name": "read_file", "arguments": json.dumps({"filepath": "/nope"})}}]}}]}],
        [{"choices": [{"delta": {"content": "<think>差不多了</think>结论: 一切正常"}}]}],
    ])

    async def fake_stream(messages, tools):
        for c in next(rounds):
            yield c

    async def fake_exec(name, args, sid):
        return ("a.py\nb.py", None) if name == "execute_shell" else ("ERROR: 文件不存在 /nope", None)

    monkeypatch.setattr(sa, "_vllm_stream", fake_stream)
    monkeypatch.setattr(sa, "execute_tool", fake_exec)
    monkeypatch.setattr(sa, "_cleanup_subagent_bg", lambda *a, **k: asyncio.sleep(0))
    run = reg.track(sa._run_subagent_core, "S1")
    out = asyncio.run(run("统计文件", "shell", ""))
    assert "一切正常" in out
    rec = reg.records("S1")[0]
    ks = _kinds(rec)
    assert ks[0] == "start" and ks[-1] == "end"
    assert ks.count("tool_start") == 2 and ks.count("tool_done") == 2
    seqs = [e["seq"] for e in rec.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    ts = [e for e in rec.events if e["kind"] == "tool_start"]
    assert ts[0]["id"] == "call_1" and ts[0]["name"] == "execute_shell"
    assert "hunter2" not in ts[0]["args"] and "***" in ts[0]["args"]
    td = [e for e in rec.events if e["kind"] == "tool_done"]
    assert td[0]["ok"] is True and "a.py" in td[0]["summary"] and td[0]["duration_ms"] >= 0
    assert td[1]["ok"] is False and td[1]["id"] == "call_2" and td[1].get("error_title")
    think = [e["text"] for e in rec.events if e["kind"] == "thinking"]
    assert "先看看目录结构" in think[0] and "差不多了" in think[-1]
    texts = [e["text"] for e in rec.events if e["kind"] == "text"]
    assert "我先列一下目录" in texts[0] and all("<think>" not in t for t in texts)
    end = rec.events[-1]
    assert end["status"] == "done" and "一切正常" in end["result"]
    v = reg.public_view(rec)
    assert v["steps"] == 2 and v["current_step"] == "已完成" and v["parent_id"] == "" and v["seq"] == rec.seq


def test_instruction_and_stop_events(ws):
    rec = reg._new_record("S2", "coder", "改 bug", background=True)
    asyncio.run(reg.agent_control_tool("S2", {"action": "send", "id": rec.id, "message": "顺便加注释"}))
    tok = reg._CURRENT.set(rec)
    try:
        msgs = [{"role": "user", "content": "任务"}]
        reg.tick(msgs, 0, 10, "coder")
    finally:
        reg._CURRENT.reset(tok)
    ins = [e for e in rec.events if e["kind"] == "instruction"]
    assert [e["state"] for e in ins] == ["queued", "delivered"] and ins[1]["text"] == "顺便加注释"
    asyncio.run(reg.agent_control_tool("S2", {"action": "stop", "id": rec.id}))
    assert rec.events[-1]["kind"] == "notice" and "停止" in rec.events[-1]["text"]
    assert reg.public_view(rec)["current_step"] == "停止中"


def test_ring_buffer_and_after(ws):
    rec = reg._new_record("S3", "researcher", "搜", background=True)
    for i in range(sev.EVENT_CAP + 50):
        sev.emit(rec, "text", text=f"第 {i} 条")
    assert len(rec.events) == sev.EVENT_CAP
    d = sev.since(rec, 0)
    assert len(d["items"]) == sev.EVENT_CAP and d["first_seq"] == rec.seq - sev.EVENT_CAP + 1
    assert d["last_seq"] == rec.seq and d["more"] is False
    d2 = sev.since(rec, rec.seq - 5)
    assert [e["seq"] for e in d2["items"]] == list(range(rec.seq - 4, rec.seq + 1))
    assert sev.since(rec, rec.seq)["items"] == []
    d3 = sev.since(rec, 0, limit=10)
    assert len(d3["items"]) == 10 and d3["more"] is True


def test_persist_and_reload_after_restart(ws):
    rec = reg._new_record("S4", "coder", "前台任务", background=False)
    sev.emit(rec, "tool_start", id="c1", name="write_file", args="{}")
    sev.emit(rec, "tool_done", id="c1", name="write_file", ok=True, duration_ms=12, summary="写好了")
    reg._finish(rec, "done", "完成")
    seq, rid = rec.seq, rec.id
    f = ws / "telemetry" / "subagent_events" / "S4" / f"{rid}.jsonl"
    assert f.exists() and len(f.read_text().splitlines()) == len(rec.events)
    reg._RECORDS.clear()
    reg._RESTORED = False      # 模拟重启
    r2 = reg.get("S4", rid)
    assert r2 is not None and r2.restored and r2.background is False
    d = sev.since(r2, 0)
    assert [e["kind"] for e in d["items"]] == ["start", "tool_start", "tool_done", "end"]
    assert d["last_seq"] == seq
    assert sev.since(r2, 2)["items"][0]["seq"] == 3


def test_nested_parent_id(ws):
    async def child_run(task, agent_type="coder", context="", sse_emit=None, **kw):
        return "子结果"

    async def parent_run(task, agent_type="coder", context="", sse_emit=None, **kw):
        await reg.track(child_run, "S5")("子任务", "explorer", "")
        return "父结果"

    asyncio.run(reg.track(parent_run, "S5")("父任务", "coder", ""))
    rs = reg.records("S5")
    parent = next(r for r in rs if r.task == "父任务")
    child = next(r for r in rs if r.task == "子任务")
    assert child.parent_id == parent.id and parent.parent_id == ""
    assert reg.public_view(child)["parent_id"] == parent.id
    assert rs[0].events[0]["kind"] == "start"


# ═══════════════════ 接口: 会话隔离 / 增量 ═══════════════════

@pytest.fixture
def gw(ws, monkeypatch):
    from gateway import _deps, subagents as gw_sub
    monkeypatch.setattr(_deps, "_srv", lambda: SimpleNamespace(TOKEN=TOKEN))
    app = FastAPI()
    app.include_router(gw_sub.router)

    def call(method, path, **kw):
        async def _go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv") as c:
                return await c.request(method, path, headers={"Authorization": f"Bearer {TOKEN}"}, **kw)
        return asyncio.run(_go())
    return call


def test_events_endpoint_and_isolation(gw):
    a = reg._new_record("A", "coder", "A 的任务", background=True)
    sev.emit(a, "text", text="一")
    sev.emit(a, "text", text="二")
    r = gw("GET", f"/v1/sessions/A/subagents/{a.id}/events", params={"after": 1})
    assert r.status_code == 200
    d = r.json()
    assert [e["seq"] for e in d["items"]] == [2, 3] and d["last_seq"] == 3 and d["done"] is False
    assert d["item"]["id"] == a.id and "worktree" in d["item"]
    for m, tail in (("GET", "/events"), ("GET", "/diff"), ("POST", "/merge"), ("POST", "/discard")):
        assert gw(m, f"/v1/sessions/B/subagents/{a.id}{tail}").status_code == 404, tail
    nd = gw("GET", f"/v1/sessions/A/subagents/{a.id}/diff").json()
    assert nd["ok"] is False and "工作树" in nd["error"]


# ═══════════════════ 分支操作 (真 git) ═══════════════════

@pytest.fixture
def repo(ws):
    p = ws / "proj"
    p.mkdir()
    _git(p, "init", "-q", "-b", "main")
    _git(p, "config", "user.name", "t")
    _git(p, "config", "user.email", "t@t")
    (p / "README.md").write_text("# demo\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-q", "-m", "init")
    return p


def _agent_with_change(repo, sid="W", line="新增一行\n", monkeypatch=None):
    rec = reg._new_record(sid, "coder", "README 加一行", background=True)
    ctx = asyncio.run(wt.create_worktree(repo, repo, "coder", "README 加一行"))
    rec.worktree_id = ctx.wid
    with open(ctx.wt_root / "README.md", "a") as f:
        f.write(line)
    return rec, ctx


def test_review_mode_keeps_branch_then_merge(repo, monkeypatch, gw):
    monkeypatch.setattr(wt, "auto_merge_enabled", lambda wid="": False)
    rec, ctx = _agent_with_change(repo)
    meta = asyncio.run(wt.finalize_worktree(ctx, "README 加一行", merge=True))
    assert meta["status"] == "committed" and meta["review"] is True and meta["base_branch"] == "main"
    assert "等用户" in wt.format_report(meta, short=True)
    reg._finish(rec, "done", "加好了")
    info = reg.public_view(rec)["worktree"]
    assert info["branch"] == ctx.branch and info["files"] == 1 and info["status"] == "committed"
    d = gw("GET", f"/v1/sessions/W/subagents/{rec.id}/diff").json()
    assert d["ok"] and d["files"] == ["README.md"] and "+新增一行" in d["diff"]
    m = gw("POST", f"/v1/sessions/W/subagents/{rec.id}/merge").json()
    assert m["ok"] is True, m
    assert "新增一行" in (repo / "README.md").read_text()
    assert ctx.branch not in _git(repo, "branch", "--list")
    assert m["item"]["worktree"]["status"] == "merged"
    # 合并后还能看改动 (分支已删, 用记下的 commit)
    d2 = gw("GET", f"/v1/sessions/W/subagents/{rec.id}/diff").json()
    assert "+新增一行" in d2["diff"]
    again = gw("POST", f"/v1/sessions/W/subagents/{rec.id}/merge").json()
    assert again["ok"] is False and "无需合并" in again["error"]


def test_diff_after_merge_when_agent_committed_itself(repo, monkeypatch):
    """子代理自己在工作树里 git commit 了 (框架没东西可补提交, meta 无 commit): 合并后看改动仍有内容。"""
    monkeypatch.setattr(wt, "auto_merge_enabled", lambda wid="": False)
    rec, ctx = _agent_with_change(repo, line="自己提交的行\n")
    _git(ctx.wt_root, "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qam", "agent self commit")
    meta = asyncio.run(wt.finalize_worktree(ctx, "t", merge=True))
    assert meta["status"] == "committed" and not meta.get("commit")
    reg._finish(rec, "done", "ok")
    assert asyncio.run(br.merge(rec))["ok"] is True
    d = asyncio.run(br.diff(rec))
    assert d["files"] == ["README.md"] and "+自己提交的行" in d["diff"]


def test_merge_conflict_rolls_back(repo, monkeypatch):
    monkeypatch.setattr(wt, "auto_merge_enabled", lambda wid="": False)
    rec, ctx = _agent_with_change(repo, line="子代理的行\n")
    asyncio.run(wt.finalize_worktree(ctx, "t", merge=True))
    reg._finish(rec, "done", "ok")
    (repo / "README.md").write_text("# demo\n主线的行\n")
    _git(repo, "commit", "-qam", "主线改动")
    head = _git(repo, "rev-parse", "HEAD")
    out = asyncio.run(br.merge(rec))
    assert out["ok"] is False and out["conflicts"] == ["README.md"] and "撤销" in out["error"]
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert ctx.branch in _git(repo, "branch", "--list")       # 分支保留, 可再处理


def test_merge_refuses_running_and_wrong_base(repo, monkeypatch):
    monkeypatch.setattr(wt, "auto_merge_enabled", lambda wid="": False)
    rec, ctx = _agent_with_change(repo)
    assert "还在运行" in asyncio.run(br.merge(rec))["error"]
    assert "还在运行" in asyncio.run(br.discard(rec))["error"]
    asyncio.run(wt.finalize_worktree(ctx, "t", merge=True))
    reg._finish(rec, "done", "ok")
    _git(repo, "checkout", "-q", "-b", "other")
    out = asyncio.run(br.merge(rec))
    assert out["ok"] is False and "基分支 main" in out["error"]
    assert "新增一行" not in (repo / "README.md").read_text()


def test_discard(repo, monkeypatch):
    monkeypatch.setattr(wt, "auto_merge_enabled", lambda wid="": False)
    rec, ctx = _agent_with_change(repo)
    asyncio.run(wt.finalize_worktree(ctx, "t", merge=True))
    reg._finish(rec, "done", "ok")
    out = asyncio.run(br.discard(rec))
    assert out["ok"] is True and out["worktree"]["status"] == "discarded"
    assert ctx.branch not in _git(repo, "branch", "--list")
    assert not ctx.wt_root.exists()
    assert "新增一行" not in (repo / "README.md").read_text()
    assert any(e["kind"] == "notice" and "丢弃" in e["text"] for e in rec.events)


def test_running_worktree_live_file_count_and_diff(repo):
    rec, ctx = _agent_with_change(repo)
    (ctx.wt_root / "new.txt").write_text("hi\n")
    info = sev.worktree_info(rec)
    assert info["status"] == "running" and info["files"] == 2
    d = asyncio.run(br.diff(rec))
    assert set(d["files"]) == {"README.md", "new.txt"} and "+hi" in d["diff"]


def test_static_frontend():
    js = ROOT / "web_assets" / "subagent_view.js"
    src = js.read_text("utf-8")
    import re
    assert not re.search(r"\b(alert|confirm|prompt)\s*\(", src)
    import shutil
    if shutil.which("node"):
        r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    html = (ROOT / "web_ui.html").read_text("utf-8")
    assert "/assets/subagent_view.js" in html and "/assets/subagent_view.css" in html


def test_auto_merge_default_by_session(monkeypatch):
    """没显式配置时: 网页会话先审后合, DAG/定时/微信等无人审阅场景自动合并; 显式配置优先。"""
    import lib.config as cfg
    from lib.agent import subagent_registry as reg
    monkeypatch.setattr(cfg, "CFG", {"agent": {}})
    rec_web = SimpleNamespace(sid="web-abc", worktree_id="w1")
    rec_dag = SimpleNamespace(sid="dag-xyz", worktree_id="w2")
    monkeypatch.setattr(reg, "_RECORDS", {"a": rec_web, "b": rec_dag})
    assert wt.auto_merge_enabled("w1") is False
    assert wt.auto_merge_enabled("w2") is True
    assert wt.auto_merge_enabled("unknown") is True
    monkeypatch.setattr(cfg, "CFG", {"agent": {"worktree_auto_merge": True}})
    assert wt.auto_merge_enabled("w1") is True
