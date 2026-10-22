"""后台子代理结果落盘 + 重启恢复 (lib/agent/subagent_registry.py) 的回归用例。

覆盖: 结束后落盘; 模拟"重启"(清空 _RECORDS + 复位 _RESTORED) 后经 agent_control result/list/status
仍能取到且内容一致; 残留运行态文件恢复后标 interrupted; 会话隔离 (A 的落盘 B 取不到);
TTL 过期清理; 每会话总量上限淘汰; result 超大被截断; sid 含异常字符不穿越目录。

直接走 registry 层 (而非整条 LLM 派发链路), 因为落盘/恢复逻辑都在 registry 内部。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.agent import subagent_registry as reg  # noqa: E402


@pytest.fixture
def ws(tmp_path):
    """工作区指向 tmp; 初始视为已恢复 (空), 需要时测试自己复位 _RESTORED 模拟重启。"""
    old_over, old_restored, old_cap = reg.WORKSPACE_OVERRIDE, reg._RESTORED, reg.MAX_PER_SESSION
    reg._RECORDS.clear()
    reg.WORKSPACE_OVERRIDE = tmp_path
    reg._RESTORED = True
    yield tmp_path
    reg.WORKSPACE_OVERRIDE = old_over
    reg._RESTORED = old_restored
    reg.MAX_PER_SESSION = old_cap
    reg._RECORDS.clear()


def _mk(sid, task="任务", agent_type="coder", background=True):
    return reg._new_record(sid, agent_type, task, background=background)


def _finish(rec, status="done", result="结论 OK"):
    reg._finish(rec, status, result)


def _file(ws, sid, rid):
    return ws / "telemetry" / "bg_agents" / reg._safe_name(sid) / f"{reg._safe_name(rid)}.json"


def _restart():
    """模拟服务重启: 丢掉内存记录, 让下次读触发从磁盘恢复。"""
    reg._RECORDS.clear()
    reg._RESTORED = False


def _ctl(sid, **args):
    return asyncio.run(reg.agent_control_tool(sid, args))


# ── 后台子代理结束 → 结果落盘到 <ws>/telemetry/bg_agents/<sid>/<id>.json ──
def test_persist_on_finish(ws):
    rec = _mk("sid-A", task="后台探索 A", agent_type="explorer")
    _finish(rec, "done", "[已验证]\nA 的结论")
    f = _file(ws, "sid-A", rec.id)
    assert f.exists(), f
    d = json.loads(f.read_text("utf-8"))
    assert d["id"] == rec.id and d["sid"] == "sid-A" and d["agent_type"] == "explorer"
    assert d["status"] == "done" and d["result"] == "[已验证]\nA 的结论" and d["background"] is True
    assert d["result_truncated"] is False and d["ended"]
    # 运行中的记录不落盘
    run = _mk("sid-A", task="还在跑")
    assert not _file(ws, "sid-A", run.id).exists()
    # [subagent-view] 前台 (阻塞) 子代理也落盘 (界面回看 / 重启后可进入), 结果已取走, 不进 [SYSTEM-AGENTS] 提示
    blk = _mk("sid-A", task="阻塞", background=False)
    blk.fetched = True
    _finish(blk, "done", "阻塞结论")
    d2 = json.loads(_file(ws, "sid-A", blk.id).read_text("utf-8"))
    assert d2["background"] is False and d2["fetched"] is True


# ── 重启后 result / list / status 仍能取到该结果且内容一致 ──
def test_restart_restore_roundtrip(ws):
    rec = _mk("sid-R", task="重启探索", agent_type="researcher")
    _finish(rec, "done", "[已验证]\n重启也能取到的结论")
    rid = rec.id
    _restart()
    # list 看得到
    lst = _ctl("sid-R", action="list")
    assert rid in lst and "重启探索" in lst, lst
    # status 看得到
    st = _ctl("sid-R", action="status", id=rid)
    assert rid in st and "已完成" in st, st
    # result 内容与落盘前一致
    res = _ctl("sid-R", action="result", id=rid)
    assert "[已验证]\n重启也能取到的结论" in res, res
    # 恢复记录标 restored, 状态原样
    r = reg.get("sid-R", rid)
    assert r is not None and r.restored and r.status == "done" and r.background


# ── 残留的"运行中"落盘文件, 重启后标 interrupted, 不显示成仍在跑 ──
def test_running_file_marked_interrupted(ws):
    sid, rid = "sid-I", "sa-abc123"
    f = _file(ws, sid, rid)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"id": rid, "sid": sid, "agent_type": "coder", "task": "残留运行",
                             "background": True, "status": "running", "started": time.time() - 10,
                             "ended": time.time() - 5, "result": None}), "utf-8")
    _restart()
    r = reg.get(sid, rid)
    assert r is not None and r.status == "interrupted" and r.restored
    # 不会被当成"运行中"
    assert r not in reg.running_background(sid)
    st = _ctl(sid, action="status", id=rid)
    assert "已中断" in st and "运行中" not in st, st


# ── 会话隔离: A 的落盘, B 重启后取不到 ──
def test_session_isolation_on_disk(ws):
    ra = _mk("sid-A", task="A 私有")
    _finish(ra, "done", "A 的机密结论")
    rid = ra.id
    _restart()
    # B 看不到也取不到
    assert _ctl("sid-B", action="list") == "本会话没有子代理记录。"
    assert reg.get("sid-B", rid) is None
    bad = _ctl("sid-B", action="result", id=rid)
    assert bad.startswith("ERROR: 找不到子代理") and rid in bad, bad
    # A 自己能取到
    assert "A 的机密结论" in _ctl("sid-A", action="result", id=rid)


# ── TTL 过期清理: 未取结果 24h / 其余 1h 过期即删文件, 不再恢复 ──
def test_ttl_cleanup(ws):
    # 已取走的普通记录: 超 RECORD_TTL 过期
    r1 = _mk("sid-T", task="已取")
    _finish(r1, "done", "x")
    reg._take(r1)   # fetched=True
    d = json.loads(_file(ws, "sid-T", r1.id).read_text("utf-8"))
    d["ended"] = time.time() - reg.RECORD_TTL - 1
    _file(ws, "sid-T", r1.id).write_text(json.dumps(d), "utf-8")
    # 未取走的后台结果: 还没到 24h, 不删
    r2 = _mk("sid-T", task="未取")
    _finish(r2, "done", "y")
    d2 = json.loads(_file(ws, "sid-T", r2.id).read_text("utf-8"))
    d2["ended"] = time.time() - reg.RECORD_TTL - 1   # >1h 但 <24h
    _file(ws, "sid-T", r2.id).write_text(json.dumps(d2), "utf-8")

    removed = reg._cleanup_disk()
    assert removed == 1
    assert not _file(ws, "sid-T", r1.id).exists()     # 已取 + 过 1h → 删
    assert _file(ws, "sid-T", r2.id).exists()         # 未取 + <24h → 留
    _restart()
    assert reg.get("sid-T", r1.id) is None
    assert reg.get("sid-T", r2.id) is not None


# ── 总量上限: 每会话磁盘最多保留 MAX_PER_SESSION 条, 超出按时间淘汰最旧 ──
def test_per_session_cap(ws):
    import os
    reg.MAX_PER_SESSION = 100   # 先不触发 per-finish 淘汰, 落满 4 条
    ids = []
    for i in range(4):
        r = _mk("sid-C", task=f"任务{i}")
        _finish(r, "done", f"结论{i}")
        ids.append(r.id)
        os.utime(_file(ws, "sid-C", r.id), (time.time() + i, time.time() + i))   # mtime 严格递增
    # 收紧上限到 2, 清理按时间淘汰最旧两条
    reg.MAX_PER_SESSION = 2
    removed = reg._cleanup_disk()
    assert removed == 2, removed
    files = list((ws / "telemetry" / "bg_agents" / reg._safe_name("sid-C")).glob("*.json"))
    assert len(files) == 2, [f.name for f in files]
    kept = {json.loads(f.read_text("utf-8"))["id"] for f in files}
    assert kept == {ids[2], ids[3]}, kept
    # per-finish 也会即时淘汰: 再落一条, 仍只保留 2 条 (最新)
    r4 = _mk("sid-C", task="任务4")
    _finish(r4, "done", "结论4")
    files = list((ws / "telemetry" / "bg_agents" / reg._safe_name("sid-C")).glob("*.json"))
    assert len(files) == 2, [f.name for f in files]


# ── result 超 64KB 落盘时截断并标注; 内存里 result 保持完整 ──
def test_result_truncation(ws):
    big = "数" * (reg.MAX_RESULT_BYTES)   # 每字 3 字节 → 远超上限
    rec = _mk("sid-X", task="大结果")
    _finish(rec, "done", big)
    # 内存里完整
    assert reg.get("sid-X", rec.id).result == big
    d = json.loads(_file(ws, "sid-X", rec.id).read_text("utf-8"))
    assert d["result_truncated"] is True
    assert len(d["result"].encode("utf-8")) <= reg.MAX_RESULT_BYTES + 200
    assert "结果过长已截断" in d["result"]
    _restart()
    r = reg.get("sid-X", rec.id)
    assert r.result_truncated and "结果过长已截断" in r.result


# ── sid 含异常字符 (路径穿越) 被清洗, 文件不逃出 bg_agents 目录 ──
def test_sid_traversal_sanitized(ws):
    evil = "../../etc/pwn"
    rec = _mk(evil, task="穿越尝试")
    _finish(rec, "done", "结论")
    root = ws / "telemetry" / "bg_agents"
    # 落在 bg_agents 内清洗后的子目录, 不在 ws/etc 之类
    f = _file(ws, evil, rec.id)
    assert f.exists() and root in f.parents
    assert not (ws / "etc").exists()
    # 清洗后的目录名里没有分隔符或 ..
    safe = reg._safe_name(evil)
    assert "/" not in safe and ".." not in safe
    # 会话隔离仍按原始 sid 判定, 可正常取回
    _restart()
    assert "结论" in _ctl(evil, action="result", id=rec.id)
    assert reg.get("sid-other", rec.id) is None
