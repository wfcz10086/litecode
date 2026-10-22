"""[trace 2026-10-22] 对话调用链路 (tool_done / tool_progress / notice) + 友好报错 (lib/friendly_errors.py)。

覆盖: 分类与脱敏、tool_done 事件内容与 SSE 序列、web_ui 侧收集与落库 (含重导合并)、旧客户端兼容。
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(ROOT / "lib")]

from lib import friendly_errors as FE  # noqa: E402
from lib import tool_trace as TT  # noqa: E402


# ── 分类 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,tool,kind", [
    ("ConnectError: [Errno 111] Connection refused", None, "network"),
    ("vLLM unreachable after 4 attempts: All connection attempts failed", None, "network"),
    ("httpx.ReadTimeout: timed out", None, "timeout"),
    ("vLLM read timeout after 4 attempts", None, "timeout"),
    ("vLLM returned 401: {\"error\":\"Invalid API key\"}", None, "auth"),
    ("HTTP 403 Forbidden", None, "auth"),
    ("vLLM returned 429: rate limit exceeded", None, "rate_limit"),
    ("vLLM returned 502: Bad Gateway", None, "upstream_5xx"),
    ("vLLM returned 503: server busy", None, "upstream_5xx"),
    ("vLLM returned 404: {\"error\":{\"message\":\"The model `nope-x` does not exist\"}}", None, "model_unavailable"),
    ("vLLM returned 400: This model's maximum context length is 204800 tokens", None, "context_overflow"),
    ("ERROR: [Errno 2] No such file or directory: '/tmp/nope.txt'", "read_file", "file_not_found"),
    ("ERROR: File not found: /tmp/nope.txt", "read_file", "file_not_found"),
    ("ERROR: [Errno 13] Permission denied: '/root/x'", "write_file", "permission"),
    ("OSError: [Errno 28] No space left on device", None, "disk_full"),
    ("ERROR: ComfyUI prompt failed: Value not in list: ckpt_name: 'sdxl.safetensors'", None, "comfyui_model_missing"),
    ("ERROR: ComfyUI queue busy, 12 pending", None, "comfyui_busy"),
    ("ERROR: ssh: Could not resolve hostname nohost-zz: Name or service not known", "ssh_exec", "ssh_unreachable"),
    ("ERROR: 未知主机别名 nohost-zz, 未配置", "ssh_exec", "ssh_unreachable"),
    ("ERROR: ssh_exec 需要 password 或 key_file", "ssh_exec", "ssh_unreachable"),   # 真实: 别名没保存过凭据
    ("ERROR: ssh_exec gaierror: [Errno -2] Name or service not known", "ssh_exec", "ssh_unreachable"),
    ("ERROR: 用户已点停止, 本工具未执行。", "execute_shell", "user_interrupt"),
    ("ERROR: 工具参数非法 JSON", None, "bad_args"),
    ("something weird happened", None, "unknown"),
])
def test_classify_kinds(raw, tool, kind):
    fe = FE.classify(raw, tool=tool)
    assert fe["kind"] == kind, fe
    assert fe["title"] and fe["hint"]
    assert set(fe["actions"]) <= set(FE.ACTIONS)
    assert fe["kind"] in FE.kinds()


def test_classify_exception_and_status():
    fe = FE.classify(TimeoutError("read timed out"))
    assert fe["kind"] == "timeout" and "TimeoutError" in fe["details"]
    assert FE.classify("", status=503)["kind"] == "upstream_5xx"
    assert FE.classify(None)["kind"] == "unknown"


def test_actions_per_kind():
    assert "switch_model" in FE.classify("model `x` does not exist")["actions"]
    assert "open_settings" in FE.classify("401 Unauthorized")["actions"]
    assert FE.classify("Connection refused")["actions"][0] == "retry"


def test_scrub_secrets_in_details():
    raw = ("vLLM returned 401: invalid key sk-abcdefgh-ijklmnop1234567890 Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abc.def "
           "url=https://x.com/v1?api_key=SECRET123&q=1 password=hunter22 \"token\": \"tok_live_998877\" "
           "sshpass -p S3cret ssh root@h https://user:pa55@host/x")
    fe = FE.classify(raw)
    d = fe["details"]
    for secret in ("sk-abcdefgh-ijklmnop1234567890", "eyJhbGciOiJIUzI1NiJ9abc", "SECRET123", "hunter22",
                   "tok_live_998877", "S3cret", "pa55"):
        assert secret not in d, (secret, d)
    assert "401" in d and "https://x.com/v1" in d          # 非敏感信息保留
    assert len(FE.classify("x" * 9000)["details"]) < FE.DETAILS_MAX + 50


def test_empty_reply_payload():
    fe = FE.empty_reply("LLM 返回空 api_key=abcdef123")
    assert fe["kind"] == "empty_reply" and "abcdef123" not in fe["details"]


# ── SSE 序列化 / 兼容 ───────────────────────────────────────────

def _delta(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[6:])["choices"][0]["delta"]


def test_sse_error_backward_compatible():
    from lib.sse import sse_error
    old = _delta(sse_error("LLM 返回空", retry_hint=True, model="m"))
    assert old == {"error": {"reason": "LLM 返回空", "retry_hint": True}}     # 老调用形状不变
    fe = FE.classify("Connection refused")
    new = _delta(sse_error(fe["title"], model="m", friendly={**fe, "content_echo": "连不上"}))["error"]
    assert new["reason"] == fe["title"] and new["retry_hint"] is True
    assert new["kind"] == "network" and new["actions"] and new["content_echo"] == "连不上"


def test_sse_notice():
    from lib.sse import sse_notice
    assert _delta(sse_notice("已自动重试 1/3", level="warn", kind="retry", model="m")) == {
        "notice": {"text": "已自动重试 1/3", "level": "warn", "kind": "retry"}}


def test_tool_done_payload():
    ok = TT.tool_done("c1", "read_file", "hello\n[SYSTEM: 给模型看的]", 1.234)
    assert ok == {"id": "c1", "name": "read_file", "ok": True, "duration_ms": 1234, "summary": "hello"}
    err = TT.tool_done("c2", "read_file", "ERROR: [Errno 2] No such file or directory: '/x'", 0.01, retry_of="c0")
    assert err["ok"] is False and err["error_kind"] == "file_not_found"
    assert err["error_title"] and err["error_hint"] and err["retry_of"] == "c0"
    long = TT.tool_done("c3", "execute_shell", "y" * 5000 + " sk-abcdefgh-ijklmnopqrstu", 0)
    assert len(long["summary"]) <= TT.SUMMARY_MAX
    assert "sk-abcdefgh-ijklmnopqrstu" not in TT.summarize("key sk-abcdefgh-ijklmnopqrstu")
    assert "secret999" not in TT.args_preview({"command": "curl", "password": "secret999"})


def test_progress_queue():
    TT.drain("s1", "web_search")
    TT.report("s1", "web_search", "搜索源 A 失败, 改用 B")
    TT.report("s1", "web_search", "进度", pct=150)
    TT.report(None, "web_search", "无会话忽略")
    got = TT.drain("s1", "web_search")
    assert [g["text"] for g in got] == ["搜索源 A 失败, 改用 B", "进度"] and got[1]["pct"] == 100.0
    assert TT.drain("s1", "web_search") == []


# ── tool_exec 真实跑一批工具: 事件顺序 / id 关联 / 进度透传 / retry_of ──

def _run_batch(monkeypatch, calls, impl):
    from lib.agent import tool_exec
    ctx = SimpleNamespace(messages=[{"role": "user", "content": "x"}], base_history=[], new_msgs=[],
                          session_id="trace-s1", sid_tag="tr", disabled_tools=set(), turn_context="",
                          tracer=SimpleNamespace(tool_call=lambda **kw: None, get_loop_hint=lambda: "",
                                                 should_force_break=lambda: False, reset_loop=lambda: None))
    monkeypatch.setattr(tool_exec, "_guard_tool_call", lambda *a: None)
    monkeypatch.setattr(tool_exec, "execute_tool", impl)
    state = tool_exec.ToolRunState(effective_timeout=60, error_streak=0, iter_write_success=False)
    tcs = [{"id": f"c{i}", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    frames, results = [], []

    async def run():
        async for f in tool_exec._run_tool_calls(
                ctx, state, tcs, results, SimpleNamespace(action_tool_calls=0),
                None, None, None, SimpleNamespace(take=lambda *a: None),
                None, None, [], SimpleNamespace(check=lambda *a: (False, None, "sig")),
                SimpleNamespace(t0=time.time()), False, "test-model"):
            frames.append(f)
    asyncio.run(run())
    return [_delta(f) for f in frames if f.startswith("data: ")], results


def test_tool_exec_emits_tool_done_sequence(monkeypatch):
    async def impl(name, args, sid=None):
        if name == "web_search":
            TT.report(sid, "web_search", "搜索源 SearXNG 超时, 改用 deep_search")
            return "结果 1", None
        if name == "read_file":
            return "ERROR: [Errno 2] No such file or directory: '/tmp/nope'", None
        return "ok", None
    deltas, results = _run_batch(monkeypatch, [("web_search", {"query": "q"}),
                                               ("read_file", {"filepath": "/tmp/nope"}),
                                               ("read_file", {"filepath": "/tmp/nope"})], impl)
    keys = [next(iter(d)) for d in deltas]
    # 每个工具: executing → done(老) → [progress] → tool_done
    assert keys.count("tool_done") == 3 and keys.count("tool_progress") == 1
    ex = [d["task_exec"] for d in deltas if "task_exec" in d and d["task_exec"]["status"] == "executing"]
    assert [e["id"] for e in ex] == ["c0", "c1", "c2"]
    assert ex[0]["name"] == "web_search" and json.loads(ex[0]["args"]) == {"query": "q"}
    assert ex[0]["detail"].startswith("web_search: ")                   # 老字段不变
    done = [d["tool_done"] for d in deltas if "tool_done" in d]
    assert [x["id"] for x in done] == ["c0", "c1", "c2"]
    assert done[0]["ok"] is True and done[0]["summary"] == "结果 1"
    assert done[1]["ok"] is False and done[1]["error_kind"] == "file_not_found" and "retry_of" not in done[1]
    assert done[2]["retry_of"] == "c1"                                   # 同参数重试了失败调用
    pg = next(d["tool_progress"] for d in deltas if "tool_progress" in d)
    assert pg == {"id": "c0", "name": "web_search", "text": "搜索源 SearXNG 超时, 改用 deep_search"}
    # tool_done 在老 task_exec done 之后 (老客户端先拿到结果行)
    i_done_old = next(i for i, d in enumerate(deltas) if d.get("task_exec", {}).get("status") == "done")
    assert i_done_old < keys.index("tool_done")
    assert len(results) == 3


# ── web_ui 侧收集 + 落库 ───────────────────────────────────────

def _frame(delta):
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def test_collect_trace_steps_and_turn_error():
    from routers.chat_router import _collect
    c = {"text": "", "reasoning": "", "tools": [], "diffs": [], "usage": None}
    _collect(c, _frame({"task_exec": {"status": "executing", "detail": "web_search: q", "id": "c0",
                                      "name": "web_search", "args": "{}"}}))
    _collect(c, _frame({"tool_progress": {"id": "c0", "text": "改用 B"}}))
    _collect(c, _frame({"tool_done": {"id": "c0", "name": "web_search", "ok": True, "duration_ms": 12,
                                      "summary": "r", "junk": 1}}))
    _collect(c, _frame({"notice": {"text": "模型服务返回 503, 已自动重试 1/3", "level": "warn"}}))
    _collect(c, _frame({"error": {"reason": "连不上", "retry_hint": True, "kind": "network", "title": "网络不通",
                                  "hint": "h", "actions": ["retry"], "details": "d", "content_echo": "e"}}))
    assert c["tools"] == ["web_search: q"]                                 # 老字段照旧
    assert c["trace_steps"][0] == {"id": "c0", "name": "web_search", "ok": True, "duration_ms": 12,
                                   "summary": "r", "notes": ["改用 B"]}
    assert c["trace_steps"][1] == {"notice": "模型服务返回 503, 已自动重试 1/3", "level": "warn"}
    assert c["turn_error"]["kind"] == "network" and c["turn_error"]["content_echo"] == "e"


def test_collect_legacy_frames_unchanged():
    """旧服务端 (无新字段) 的帧: 收集结果与改动前一致, 不产生 turn_error。"""
    from routers.chat_router import _collect
    c = {"text": "", "reasoning": "", "tools": [], "diffs": [], "usage": None}
    _collect(c, _frame({"task_exec": {"status": "executing", "detail": "read_file: a.py"}}))
    _collect(c, _frame({"task_exec": {"status": "done", "detail": "import x"}}))
    _collect(c, _frame({"content": "好了"}))
    _collect(c, _frame({"error": {"reason": "LLM 返回空", "retry_hint": True}}))
    assert c["tools"] == ["read_file: a.py"] and c["text"] == "好了"
    assert c.get("trace_steps") == []
    assert c["turn_error"]["kind"] in FE.kinds() and c["turn_error"]["reason"] == "LLM 返回空"


def test_error_frame_keeps_legacy_string():
    from routers.chat_router import _error_frame
    c = {}
    f = _error_frame(c, "HTTP 503 upstream busy", 503)
    d = json.loads(f[6:])
    assert d["error"] == "HTTP 503 upstream busy"                         # CLI / 微信读这个字符串
    assert d["friendly"]["kind"] == "upstream_5xx" and c["turn_error"]["kind"] == "upstream_5xx"


def test_attach_steps_by_tool_call_id():
    a1 = {"role": "assistant", "content": "", "_tool_results": [{"id": "c0"}]}
    a2 = {"role": "assistant", "content": "", "_tool_results": [{"id": "c1"}]}
    a3 = {"role": "assistant", "content": "完成", "_tool_results": []}
    steps = [{"id": "c0", "ok": True}, {"id": "c1", "ok": False}, {"notice": "重试"}]
    TT.attach_steps([a1, a2, a3], steps, {"kind": "network"})
    assert a1["trace_steps"] == [steps[0]] and a2["trace_steps"] == [steps[1]]
    assert a3["trace_steps"] == [steps[2]] and a3["turn_error"] == {"kind": "network"}
    assert TT.turn_steps([a1, a2, a3]) == steps


def test_turn_already_imported_attaches_trace():
    import web_ui
    msgs = [{"role": "user", "content": "查一下"},
            {"role": "assistant", "content": "", "_tool_results": [{"id": "c0", "name": "web_search", "result": "r"}]},
            {"role": "assistant", "content": "答案", "_tool_results": []}]
    collected = {"text": "答案", "tools": ["web_search: q"], "diffs": [],
                 "trace_steps": [{"id": "c0", "name": "web_search", "ok": True, "duration_ms": 5}],
                 "turn_error": None}
    assert web_ui._turn_already_imported(msgs, collected) is True
    assert msgs[1]["trace_steps"][0]["duration_ms"] == 5 and "trace_steps" not in msgs[2]


def test_reimport_keeps_trace_from_cache():
    import web_ui
    old = [{"role": "user", "content": "查一下"},
           {"role": "assistant", "content": "", "_tool_results": [{"id": "c0"}],
            "trace_steps": [{"id": "c0", "ok": False, "error_kind": "network"}]},
           {"role": "assistant", "content": "失败了", "turn_error": {"kind": "network", "title": "网络不通"}}]
    new = [{"role": "user", "content": "查一下"},
           {"role": "assistant", "content": "", "_tool_results": [{"id": "c0"}]},
           {"role": "assistant", "content": "失败了", "_tool_results": []}]
    web_ui._merge_cached_turn_meta(old, new)
    assert new[1]["trace_steps"][0]["error_kind"] == "network"
    assert new[2]["turn_error"]["title"] == "网络不通"


def test_save_assistant_msg_persists_trace(monkeypatch):
    import web_ui
    store = {"id": "web-t", "messages": [{"role": "user", "content": "q"}]}
    saved = []
    monkeypatch.setattr(web_ui, "load_session", lambda sid: store)
    monkeypatch.setattr(web_ui, "save_session", lambda d: saved.append(json.loads(json.dumps(d))))
    # 只有整轮错误 (没有正文 / 工具) 也要落库, 刷新后还能看到错误卡片
    web_ui._save_assistant_msg("web-t", {"text": "", "tools": [], "diffs": [], "reasoning": "", "usage": None,
                                         "trace_steps": [], "turn_error": {"kind": "model_unavailable"}})
    last = saved[-1]["messages"][-1]
    assert last["role"] == "assistant" and last["turn_error"]["kind"] == "model_unavailable"
    store["messages"].append({"role": "user", "content": "q2"})
    web_ui._save_assistant_msg("web-t", {"text": "好", "tools": ["read_file: a"], "diffs": [], "reasoning": "",
                                         "usage": None, "trace_steps": [{"id": "c9", "ok": True}]})
    last = saved[-1]["messages"][-1]
    assert last["trace_steps"] == [{"id": "c9", "ok": True}] and "turn_error" not in last
