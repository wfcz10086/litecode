"""失败经验闭环 (lib/agent/failure_loop.py) 的离线回归。

驱动真实 agent_stream(): 在 lib.agent.llm_call._vllm_stream / lib.agent.tool_exec.execute_tool
处打桩 (拆分后真正查名字的模块), 存储目录指向临时目录, 不连任何服务。
  1. 失败回合 (工具重复熔断 / 迭代上限) → failures.jsonl 写入一条, 且已脱敏
  2. 相似任务新回合 → 模型收到的 user 消息里有 [SYSTEM-EXPERIENCE] 注入 (仅一次)
  3. 不相似任务 / 正常成功回合 → 无注入 / 无记录
  4. agent.failure_memory.enabled=false → 无记录无注入
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import litecode_server as srv  # noqa: E402
import lib.agent.llm_call as _llm_call  # noqa: E402
import lib.agent.tool_exec as _tool_exec  # noqa: E402
from lib.agent import failure_loop as fl  # noqa: E402
from lib import failure_memory as fm  # noqa: E402
from lib import turn_context as tc  # noqa: E402

TASK = "帮我把 config 解析模块重构成异步版本并补上单元测试"
SIMILAR = "帮我把 config 解析模块重构成异步版本, 再补单元测试"
UNRELATED = "今天北京天气怎么样，适合出门跑步吗"
SECRET = "sk-EXAMPLEabcdefghijklmnopqrstuvwxyz0123"   # 假密钥 (含 EXAMPLE, 公开版自检放行)


class _FakeLLM:
    """按脚本回放; 脚本走完重复最后一项。记录每次收到的 messages 副本。"""
    def __init__(self, turns):
        self.turns, self.calls, self.seen = turns, 0, []

    async def __call__(self, messages, tools, max_tokens_override=None):
        self.seen.append(json.loads(json.dumps(messages, ensure_ascii=False, default=str)))
        kind, payload = self.turns[min(self.calls, len(self.turns) - 1)]
        idx = self.calls
        self.calls += 1
        if kind == "text":
            yield {"choices": [{"delta": {"content": payload}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        else:
            name, args = payload
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"c{idx}",
                   "function": {"name": name, "arguments": json.dumps(args)}}]}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "WORKSPACE", tmp_path)

    async def _fake_tool(name, args, sid=None):
        return f"ERROR: 401 unauthorized, api_key={SECRET} 密码是 hunter2pass", None
    monkeypatch.setattr(srv, "execute_tool", _fake_tool)
    monkeypatch.setattr(_tool_exec, "execute_tool", _fake_tool)
    return tmp_path


def _set_cfg(monkeypatch, **kw):
    agent = dict(fl.CFG.get("agent") or {})
    agent["failure_memory"] = kw
    monkeypatch.setitem(fl.CFG, "agent", agent)


def _drive(monkeypatch, turns, user_message):
    fake = _FakeLLM(turns)
    monkeypatch.setattr(srv, "_vllm_stream", fake)
    monkeypatch.setattr(_llm_call, "_vllm_stream", fake)

    async def _run():
        return "".join([c async for c in srv.agent_stream(user_message, None)])
    return asyncio.run(_run()), fake


def _records(ws):
    p = Path(ws) / "telemetry" / "failures.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _user_msgs(fake):
    return [str(m.get("content")) for msgs in fake.seen for m in msgs if m.get("role") == "user"]


# 同一个 (工具, 参数) 一直重试 → guard 拦截累计 8 次 → TOOL-LOOP-BREAK
LOOP_TURNS = [("tool", ("web_fetch", {"url": "http://example.invalid/x"}))]
OK_TURNS = [("text", "好的, 已经处理完了。")]


def test_tool_loop_break_records_scrubbed_failure(env, monkeypatch):
    full, fake = _drive(monkeypatch, LOOP_TURNS, f"{TASK} (key {SECRET})")
    assert "[DONE]" in full
    recs = _records(env)
    assert len(recs) == 1, recs
    r = recs[0]
    assert r["error_type"] == "tool_loop_break"
    assert r["stuck_at"] == "web_fetch"
    assert "config 解析模块" in r["summary"]
    blob = json.dumps(recs, ensure_ascii=False)
    assert SECRET not in blob and "hunter2pass" not in blob, blob
    assert "REDACTED" in blob


def test_max_iter_records_failure(env, monkeypatch):
    monkeypatch.setattr(srv, "MAX_ITER", 3)
    # 每轮参数不同, 不触发重复熔断, 只会耗尽迭代
    turns = [("tool", ("web_fetch", {"url": f"http://example.invalid/{i}"})) for i in range(5)]
    _drive(monkeypatch, turns, TASK)
    recs = _records(env)
    assert [r["error_type"] for r in recs] == ["max_iter"]


def test_similar_task_gets_experience_injected_once(env, monkeypatch):
    _drive(monkeypatch, LOOP_TURNS, TASK)
    assert len(_records(env)) == 1
    _, fake = _drive(monkeypatch, OK_TURNS, SIMILAR)
    users = _user_msgs(fake)
    hits = [u for u in users if fl.PREFIX in u]
    assert len(hits) == 1, users
    inj = hits[0]
    assert "web_fetch" in inj and "避免" in inj
    # 注入在 <context> 段里, strip 后只剩用户原话 (界面/记忆提取都看不到)
    stripped = tc.strip([{"role": "user", "content": inj}])[0]["content"]
    assert fl.PREFIX not in stripped and stripped == SIMILAR
    # 注入文本本身 ≤ 400 字, 且以 [SYSTEM-...] 开头 (web_ui 的 _is_sys_inject_text 家族)
    exp = fl.recall_text(SIMILAR)
    assert exp.startswith("[SYSTEM-") and len(exp) <= 400
    # 成功回合不新增记录
    assert len(_records(env)) == 1


def test_unrelated_task_no_injection(env, monkeypatch):
    _drive(monkeypatch, LOOP_TURNS, TASK)
    _, fake = _drive(monkeypatch, OK_TURNS, UNRELATED)
    assert not any(fl.PREFIX in u for u in _user_msgs(fake))
    assert len(_records(env)) == 1


def test_disabled_no_record_no_injection(env, monkeypatch):
    fm.record_failure(env, "tool_loop_break", fl.task_text(TASK), "换思路", extra={"stuck_at": "web_fetch"})
    _set_cfg(monkeypatch, enabled=False)
    _, fake = _drive(monkeypatch, OK_TURNS, SIMILAR)
    assert not any(fl.PREFIX in u for u in _user_msgs(fake))
    _drive(monkeypatch, LOOP_TURNS, "另一个完全不同的长任务: 整理 docs 目录下的全部文档")
    assert len(_records(env)) == 1, "关闭时不应新增记录"


def test_threshold_configurable(env, monkeypatch):
    fm.record_failure(env, "max_iter", fl.task_text(TASK), "拆步骤")
    assert fl.recall_text(SIMILAR)
    _set_cfg(monkeypatch, threshold=0.95)
    assert fl.recall_text(SIMILAR) == ""
    _set_cfg(monkeypatch, max_inject_chars=50)
    assert 0 < len(fl.recall_text(TASK)) <= 50


def test_short_message_and_seen_dedup(env, monkeypatch):
    fm.record_failure(env, "max_iter", "继续", "拆步骤")
    assert fl.recall_text("继续") == "", "过短消息不召回"
    fm.record_failure(env, "max_iter", fl.task_text(TASK), "拆步骤")
    text = fl.recall_text(TASK)
    assert text
    hist = [{"role": "user", "content": TASK, tc.KEY: tc.render([("failure_memory", text)])}]
    assert fl.recall_source(tc.TurnInput(TASK, "", hist)) == "", "本会话已给过同一条经验不再重复"


def test_wechat_sessions_isolated(env):
    assert fl.store_dir("wx-acct-user1") == Path(env) / "sessions" / "wx-acct-user1"
    assert fl.store_dir("web-abc") == Path(env)
    assert fl.store_dir(None) == Path(env)


def test_errors_never_break_main_flow(env, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(fm, "find_similar", _boom)
    monkeypatch.setattr(fm, "record_failure", _boom)
    full, _ = _drive(monkeypatch, LOOP_TURNS, TASK)
    assert "[DONE]" in full
    assert fl.recall_text(TASK) == ""


def test_infra_errors_not_recorded_as_experience(env):
    """模型服务不可达/断流/重启这类故障与任务无关, 不能记成经验 (否则相似任务会被注入误导提示)。"""
    from types import SimpleNamespace
    task = "帮我把 config 解析模块重构成异步版本并补上单元测试"
    for detail in ("RuntimeError: vLLM unreachable after 4 attempts: All connection attempts failed",
                   "RuntimeError: 模型服务卡住了, 已自动重启, 约 1 分钟后恢复",
                   "ReadError: Server disconnected without sending a response.",
                   "RuntimeError: vLLM returned 502: bad gateway"):
        ctx = SimpleNamespace(session_id=None, sid_tag="t", new_msgs=[],
                              failure={"kind": "turn_exception", "stuck_at": "", "detail": detail})
        assert fl.record_turn(ctx, task) is False, detail
    assert _records(env) == []
    ctx = SimpleNamespace(session_id=None, sid_tag="t", new_msgs=[],
                          failure={"kind": "turn_exception", "stuck_at": "", "detail": "KeyError: 'path'"})
    assert fl.record_turn(ctx, task) is True                     # 真正的任务异常照常记录
    assert len(_records(env)) == 1
